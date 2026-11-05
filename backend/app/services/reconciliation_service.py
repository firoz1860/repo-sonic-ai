"""Bank statement upload, matching suggestions and person-approved application of payments."""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date, datetime

from ..config import Settings
from ..logic.invoices import is_outstanding, tds_variants
from ..logic.matching.engine import reconcile
from ..logic.money import CURRENCIES, D, parse_date, q2, to_float
from ..repositories.store import Store, new_id, now

REQUIRED = ["txn_date", "narration", "reference", "credit", "debit", "currency"]


class ReconError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _num(text: str) -> tuple[float | None, str | None]:
    t = (text or "").strip().replace(",", "")
    t = re.sub(r"^[₹$€£]|(?i:INR|USD|EUR|GBP)", "", t).strip()
    if t == "":
        return 0.0, None
    try:
        v = D(t)
    except Exception:  # noqa: BLE001
        return None, f"bad amount {text!r}"
    if v < 0:
        return None, "negative amount"
    return float(q2(v)), None


def _date(text: str) -> tuple[str | None, str | None]:
    t = (text or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(t, fmt).date().isoformat(), None
        except ValueError:
            continue
    return None, f"bad date {text!r}"


def parse_statement(content: bytes, max_rows: int) -> list[dict]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ReconError("CSV is empty")
    header = {h.strip().lower(): h for h in reader.fieldnames if h}
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise ReconError(f"CSV is missing columns: {', '.join(missing)}. Expected: {', '.join(REQUIRED)}")
    rows = []
    for n, raw in enumerate(reader, start=1):
        if n > max_rows:
            raise ReconError(f"statement has more than {max_rows} rows")
        r = {k: (raw.get(header[k]) or "").strip() for k in REQUIRED}
        if not any(r.values()):
            continue
        errors = []
        d, e = _date(r["txn_date"])
        errors += [e] if e else []
        credit, e = _num(r["credit"])
        errors += [e] if e else []
        debit, e = _num(r["debit"])
        errors += [e] if e else []
        cur = r["currency"].upper()
        if cur not in CURRENCIES:
            errors.append(f"unsupported currency {r['currency']!r}")
        if credit and debit:
            errors.append("row has both credit and debit")
        rows.append({"row_no": n, "txn_date": d, "narration": r["narration"][:300], "reference": r["reference"][:60],
                     "credit": credit or 0.0, "debit": debit or 0.0, "currency": cur, "errors": errors})
    if not rows:
        raise ReconError("CSV has no data rows")
    return rows


class ReconciliationService:
    def __init__(self, store: Store, settings: Settings):
        self.store, self.s = store, settings

    # ------------------------------------------------------------ upload & matching
    def upload(self, filename: str, content: bytes) -> dict:
        if len(content) > self.s.max_upload_bytes:
            raise ReconError("file too large", 413)
        rows = parse_statement(content, self.s.max_statement_rows)
        sid = new_id("stmt")
        fps = self.store.approved_fingerprints()
        txns = []
        for r in rows:
            fp = hashlib.sha256("|".join(str(r[k]) for k in ("txn_date", "narration", "reference", "credit", "debit", "currency")).encode()).hexdigest()
            t = {"txn_id": new_id("txn"), "statement_id": sid, "fingerprint": fp, **r, "status": "pending", "suggestion": None,
                 "duplicate_of": fps.get(fp)}
            if r["errors"]:
                t["status"] = "invalid"
            elif r["debit"] > 0 or r["credit"] <= 0:
                t["status"] = "ignored"
            txns.append(t)
        doc = {"statement_id": sid, "filename": re.sub(r"[^\w.\- ]", "_", filename or "statement.csv")[:120], "uploaded_at": now(), "rows": len(txns)}
        self.store.add_statement(doc, txns)
        self.rematch(sid)
        return self.statement_view(sid)

    def rematch(self, statement_id: str) -> None:
        txns = self.store.transactions(statement_id)
        todo = [t for t in txns if t["status"] in ("pending", "ignored", "rejected")]
        customers, invoices, claims = self.store.customers(), self.store.invoices(), self.store.claims()
        suggestions = reconcile([self._engine_row(t) for t in todo], customers, invoices, claims)
        for t, s in zip(todo, suggestions):
            if t.get("duplicate_of") and s["status"] != "ignored":
                s = {**s, "status": "no_match", "score": 0, "invoices": [], "allocations": [], "reason": "Same transaction was already approved earlier (duplicate row)."}
            self.store.update_transaction(t["txn_id"], suggestion=s)

    @staticmethod
    def _engine_row(t: dict) -> dict:
        return {"txn_date": t["txn_date"], "narration": t["narration"], "reference": t["reference"], "credit": t["credit"], "debit": t["debit"], "currency": t["currency"]}

    def statement_view(self, sid: str) -> dict:
        st = self.store.statement(sid)
        if not st:
            raise ReconError("statement not found", 404)
        txns = self.store.transactions(sid)
        return {**st, "transactions": txns, "summary": {s: sum(1 for t in txns if t["status"] == s) for s in ("pending", "approved", "rejected", "ignored", "invalid")}}

    # ------------------------------------------------------------ decisions
    def approve(self, txn_id: str, allocations: list[dict] | None, actor: str = "accounts") -> dict:
        txn = self.store.transaction(txn_id)
        if not txn:
            raise ReconError("transaction not found", 404)
        if txn["status"] != "pending":
            raise ReconError(f"transaction is {txn['status']}, only pending receipts can be approved", 409)
        credit = q2(txn["credit"])
        if not allocations:
            sug = txn.get("suggestion") or {}
            if sug.get("status") != "match":
                raise ReconError("no suggestion to approve - choose an invoice", 422)
            allocations = [{"invoice_number": a["invoice_number"], "amount": a["amount"],
                            "action": ("close_tds" if a["shortfall"] else None)} for a in sug["allocations"]]
        plan, total = [], D(0)
        seen = set()
        for a in allocations:
            number = str(a.get("invoice_number", "")).strip()
            inv = self.store.invoice(number)
            if not inv:
                raise ReconError(f"invoice {number} not found", 422)
            if number in seen:
                raise ReconError(f"invoice {number} appears twice", 422)
            seen.add(number)
            if inv["currency"] != txn["currency"]:
                raise ReconError(f"{number} is in {inv['currency']} but the receipt is in {txn['currency']}", 422)
            if not is_outstanding(inv):
                raise ReconError(f"{number} is {inv['status']} and cannot receive a payment", 422)
            amount = q2(a["amount"]) if a.get("amount") is not None else credit
            if amount <= 0:
                raise ReconError("amount must be positive", 422)
            bal = q2(inv["balance_due"])
            if amount > bal:
                raise ReconError(f"{number}: payment {amount} exceeds balance {bal}. Split it across invoices or choose another.", 422)
            tds_closed, warning = D(0), None
            if amount < bal:
                action = a.get("action")
                if action not in ("close_tds", "leave_open"):
                    raise ReconError(f"{number}: payment is less than the balance - choose 'Close invoice (TDS deducted)' or 'Leave balance open'", 422)
                if action == "close_tds":
                    tds_closed = bal - amount
                    if tds_closed not in tds_variants(inv):
                        warning = f"shortfall {tds_closed} differs from the expected 10% TDS ({', '.join(str(v) for v in tds_variants(inv)) or 'not applicable'})"
            plan.append((inv, amount, tds_closed, warning))
            total += amount
        if q2(total) != credit:
            raise ReconError(f"allocated {q2(total)} but the receipt is {credit}; they must be equal", 422)
        if not self.store.claim_transaction(txn_id, "pending", "approving"):
            raise ReconError("transaction is being processed", 409)
        snapshots, payments = [], []
        try:
            for inv, amount, tds_closed, warning in plan:
                snap = self.store.invoice(inv["invoice_number"])
                updated = self.store.apply_to_invoice(inv["invoice_number"], float(amount), float(tds_closed))
                if updated is None:
                    raise ReconError(f"{inv['invoice_number']} changed while approving; refresh and try again", 409)
                snapshots.append(snap)
                payments.append({"payment_id": new_id("pay"), "txn_id": txn_id, "statement_id": txn["statement_id"], "invoice_number": inv["invoice_number"],
                                 "customer_id": inv["customer_id"], "currency": inv["currency"], "amount": float(amount), "tds_closed": float(tds_closed),
                                 "invoice_status_after": updated["status"], "balance_after": updated["balance_due"], "approved_by": actor,
                                 "approved_at": now(), "warning": warning, "txn_date": txn["txn_date"], "reference": txn["reference"]})
        except Exception:
            for snap in reversed(snapshots):
                self.store.restore_invoice(snap)
            self.store.update_transaction(txn_id, status="pending")
            raise
        for p in payments:
            self.store.add_payment(p)
        self.store.update_transaction(txn_id, status="approved", applied=[{k: p[k] for k in ("invoice_number", "amount", "tds_closed", "invoice_status_after", "balance_after", "warning")} for p in payments], approved_at=now())
        return self.store.transaction(txn_id)

    def reject(self, txn_id: str) -> dict:
        if not self.store.claim_transaction(txn_id, "pending", "rejected"):
            raise ReconError("only pending receipts can be rejected", 409)
        return self.store.transaction(txn_id)

    def reopen(self, txn_id: str) -> dict:
        if not self.store.claim_transaction(txn_id, "rejected", "pending"):
            raise ReconError("only rejected receipts can be reopened", 409)
        return self.store.transaction(txn_id)
