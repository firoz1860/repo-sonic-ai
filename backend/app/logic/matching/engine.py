"""Bank-statement reconciliation engine.

Pure functions, no I/O. For every statement receipt we generate candidate invoice sets (single invoice, TDS-adjusted,
part payment, several invoices) from the customers whose name resembles the payer, score each candidate by adding
log-odds evidence (invoice number, payer similarity, amount case, date plausibility, email evidence) and convert the
sum to a 0-100 confidence. Nothing here applies a payment; a person approves every suggestion.
"""
from __future__ import annotations

import math
import re
from datetime import date
from decimal import Decimal
from itertools import combinations, product

from ..invoices import is_outstanding, tds_variants
from ..money import D, format_amount, parse_date, q2
from .normalize import parse_narration
from .similarity import TfidfIndex, payer_similarity

MATCH_THRESHOLD = 50
BASE = -2.0


def _clip(v, lo, hi):
    return max(lo, min(hi, v))


def _score(x: float) -> int:
    return int(_clip(round(100 / (1 + math.exp(-(x - 2.6) / 1.4))), 1, 99))


def _norm_ref(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


class Context:
    def __init__(self, customers, invoices, claims=None):
        self.customers = {c["customer_id"]: c for c in customers}
        self.invoices = {i["invoice_number"]: i for i in invoices}
        self.claims = claims or []
        self.known_numbers = set(self.invoices)
        self.known_pos = {i["po_number"] for i in invoices if i.get("po_number")}
        self.index = TfidfIndex([c["name"] for c in customers])


def _open_for(ctx: Context, customer_id: str, currency: str, virtual: dict) -> list[dict]:
    out = []
    for inv in ctx.invoices.values():
        if inv["customer_id"] == customer_id and inv["currency"] == currency and is_outstanding(inv):
            if virtual.get(inv["invoice_number"], D(inv["balance_due"])) > 0:
                out.append(inv)
    return sorted(out, key=lambda i: (i["due_date"], i["invoice_number"]))


def _bal(inv, virtual) -> Decimal:
    return virtual.get(inv["invoice_number"], D(inv["balance_due"]))


def _classify(invs: list[dict], amount: Decimal, virtual: dict, currency: str):
    """Return (case, allocations, shortfall_total) or None if the amount cannot be explained."""
    bals = [_bal(i, virtual) for i in invs]
    if sum(bals) == amount:
        kind = "exact" if len(invs) == 1 else "multi_exact"
        return kind, [(i, b, Decimal(0)) for i, b in zip(invs, bals)], Decimal(0)
    # TDS: each Indian invoice may be paid net of 10% TDS
    opts = []
    for inv, b in zip(invs, bals):
        variants = tds_variants(inv)
        opts.append([(b - t, t) for t in variants if 0 < t <= b])
    if all(opts):
        for combo in product(*opts):
            if sum(c[0] for c in combo) == amount:
                kind = "tds" if len(invs) == 1 else "multi_tds"
                return kind, [(i, c[0], c[1]) for i, c in zip(invs, combo)], sum(c[1] for c in combo)
    if len(invs) == 1 and amount < bals[0]:
        return "partial", [(invs[0], amount, Decimal(0))], Decimal(0)
    if amount > sum(bals):
        return "over", [(i, b, Decimal(0)) for i, b in zip(invs, bals)], Decimal(0)
    return None


def _claims_for(ctx, txn, customer_id, inv_numbers):
    ref = _norm_ref(txn.get("reference"))
    found = []
    for c in ctx.claims:
        if customer_id and c.get("customer_id") != customer_id:
            if not (ref and _norm_ref(c.get("reference")) == ref):
                continue
        exact_ref = bool(ref) and _norm_ref(c.get("reference")) == ref
        overlap = set(c.get("invoice_numbers", [])) & set(inv_numbers)
        near_date = False
        if c.get("claimed_date") and txn.get("txn_date"):
            near_date = abs((parse_date(c["claimed_date"]) - parse_date(txn["txn_date"])).days) <= 3
        if exact_ref or (overlap and near_date):
            found.append({"claim": c, "exact_ref": exact_ref, "overlap": bool(overlap)})
    return found


def suggest_one(txn: dict, ctx: Context, virtual: dict | None = None, today: date | None = None) -> dict:
    virtual = virtual or {}
    credit, debit = D(txn.get("credit")), D(txn.get("debit"))
    if credit <= 0:
        return _result("ignored", 0, "Money going out (debit) - not a customer receipt.")
    parsed = parse_narration(txn.get("narration", ""), ctx.known_numbers, ctx.known_pos)
    currency, amount = txn["currency"], q2(credit)
    tdate = parse_date(txn["txn_date"])
    refs, notes = parsed["invoice_refs"], []

    if parsed["non_customer"] and not refs:
        return _result("no_match", 0, "Bank interest, charge or other non-customer credit.", parsed=parsed)

    for r in refs:
        inv = ctx.invoices[r]
        if not is_outstanding(inv):
            notes.append(f"{r} is already {inv['status']}" + (" (possible duplicate/overpayment)" if inv["status"] == "paid" else ""))
    for u in parsed["unknown_refs"]:
        notes.append(f"{u} does not exist")

    # payer similarity for every customer billed in this currency
    payer = {}
    for cid, c in ctx.customers.items():
        if c["currency"] == currency:
            payer[cid] = payer_similarity(parsed["payer_text"], c["name"], ctx.index)
    cand_customers = {cid for cid, (p, _) in payer.items() if p >= 0.35}
    for r in refs:
        if ctx.invoices[r]["currency"] == currency:
            cand_customers.add(ctx.invoices[r]["customer_id"])
    for inv in ctx.invoices.values():
        if inv.get("po_number") in parsed["po_refs"] and inv["currency"] == currency:
            cand_customers.add(inv["customer_id"])
    for c in ctx.claims:
        if _norm_ref(c.get("reference")) and _norm_ref(c.get("reference")) == _norm_ref(txn.get("reference")) and c.get("customer_id") in ctx.customers:
            if ctx.customers[c["customer_id"]]["currency"] == currency:
                cand_customers.add(c["customer_id"])

    candidates = []
    for cid in cand_customers:
        P, pdetail = payer.get(cid, (0.0, {}))
        open_invs = _open_for(ctx, cid, currency, virtual)
        if not open_invs:
            continue
        sets = [[i] for i in open_invs]
        if len(open_invs) <= 12:
            for k in (2, 3, 4):
                sets += [list(c) for c in combinations(open_invs, k)]
        ref_set = [i for i in open_invs if i["invoice_number"] in refs]
        if len(ref_set) > 1 and ref_set not in sets:
            sets.append(ref_set)
        dated_ok = [i for i in open_invs if parse_date(i["invoice_date"]) <= tdate]
        for s in sets:
            cls = _classify(s, amount, virtual, currency)
            in_refs = [i for i in s if i["invoice_number"] in refs]
            if cls is None and not in_refs:
                continue
            if cls is None:
                cls = ("mismatch", [(i, _bal(i, virtual), Decimal(0)) for i in s], Decimal(0))
            case, allocs, shortfall = cls
            if case == "over" and not in_refs:
                continue
            if case == "mismatch" and len(s) > 1:
                continue
            x, why = BASE, []
            # payer
            ref_like = bool(in_refs or parsed["po_refs"])
            if P >= 0.35:
                x += _clip(6 * (P - 0.35), -2.5, 4.0)
                why.append(f"payer matches {ctx.customers[cid]['name']} ({int(P*100)}%)")
            else:
                x += -1.0 if parsed["payer_text"] else 0
                why.append("payer name not recognised" if parsed["payer_text"] else "no payer name")
            # explicit references
            if refs:
                if in_refs:
                    x += 5.0 * len(in_refs) / max(len(s), len(refs))
                    why.insert(0, "invoice number " + ", ".join(i["invoice_number"] for i in in_refs) + " in narration")
                else:
                    x -= 4.0
            po_hit = [i for i in s if i.get("po_number") in parsed["po_refs"]]
            if po_hit:
                x += 4.0
                why.append(f"PO {po_hit[0]['po_number']} in narration")
                ref_like = True
            links = _claims_for(ctx, txn, cid, [i["invoice_number"] for i in s])
            for l in links:
                if l["exact_ref"]:
                    x += 5.0
                    why.append("bank reference quoted in customer's email")
                    ref_like = True
                elif l["overlap"]:
                    x += 1.5
                    why.append("customer's email says they paid this invoice")
            # amount
            bal_total = sum(_bal(i, virtual) for i in s)
            if case == "exact":
                x += 4.0
                why.append("amount equals invoice balance exactly")
            elif case == "multi_exact":
                x += 3.6
                why.append(f"amount equals the sum of {len(s)} invoice balances")
            elif case in ("tds", "multi_tds"):
                x += 3.5
                why.append(f"amount equals balance less 10% TDS ({format_amount(shortfall, currency)})")
            elif case == "partial":
                x += 0.2
                short = bal_total - amount
                why.append(f"part payment: {format_amount(amount, currency)} of {format_amount(bal_total, currency)} balance")
                if parsed["part_payment"]:
                    x += 1.0
                    why.append("narration says part payment")
                if currency != "INR" and short / bal_total <= Decimal("0.03"):
                    x += 1.3
                    why.append("small shortfall, likely bank charges")
                if len(dated_ok) == 1:
                    x += 0.8
            elif case == "over":
                x -= 3.0
                why.append(f"amount exceeds balance by {format_amount(amount - bal_total, currency)}")
            else:
                x -= 2.5
                why.append(f"amount differs from balance {format_amount(bal_total, currency)}")
            # dates
            gaps = [(tdate - parse_date(i["invoice_date"])).days for i in s]
            if min(gaps) < 0:
                x -= 14.0
                why.append("payment dated before the invoice")
            else:
                x += 0.5 if max(gaps) <= 180 else -1.0
            candidates.append({"x": x, "case": case, "allocs": allocs, "shortfall": shortfall, "why": why,
                               "customer_id": cid, "P": P, "pdetail": pdetail, "ref_like": ref_like, "links": links, "set": s})

    if not candidates:
        reason = "; ".join(notes) or ("Payer not recognised and no invoice reference" if parsed["payer_text"] else "No payer or reference in narration")
        hint = [i["invoice_number"] for i in ctx.invoices.values()
                if i["currency"] == currency and is_outstanding(i) and q2(_bal(i, virtual)) == amount]
        if hint and not refs:
            reason += f" (amount alone equals {', '.join(hint)}, but nothing else supports it)"
        return _result("no_match", 0, reason, parsed=parsed)

    # ambiguity: identical-amount rivals in the same customer without explicit evidence
    for c in candidates:
        if not c["ref_like"] and len(c["set"]) == 1:
            rivals = [o for o in candidates if o is not c and o["customer_id"] == c["customer_id"] and o["case"] == c["case"]
                      and len(o["set"]) == 1 and o["x"] >= c["x"] - 3.0]
            if rivals and c["case"] in ("exact", "tds"):
                c["x"] -= 0.8 * len(rivals)
                c["why"].append("same amount as " + ", ".join(o["set"][0]["invoice_number"] for o in rivals) + "; earliest due date preferred")
    # earlier due date wins ties (FIFO)
    candidates.sort(key=lambda c: (-round(c["x"], 3), min(i["due_date"] for i in c["set"]), len(c["set"])))
    best = candidates[0]
    for c in candidates:
        c["score"] = _score(c["x"])
        if c["P"] < 0.35 and not c["ref_like"]:
            c["score"] = min(c["score"], 45)
        if c["case"] == "partial" and not c["ref_like"] and c["P"] < 0.6:
            c["score"] = min(c["score"], 49)
        if c["case"] in ("over", "mismatch") and c["score"] > 49:
            c["score"] = 49
    best["score"] = best["score"]
    reason = "; ".join(best["why"][:4])
    if notes and best["score"] < MATCH_THRESHOLD:
        reason += " (" + "; ".join(notes) + ")"
    allocations = [{"invoice_number": i["invoice_number"], "amount": float(a), "shortfall": float(sf)} for i, a, sf in best["allocs"]]
    kind = best["case"]
    out = {
        "status": "match" if best["score"] >= MATCH_THRESHOLD else "no_match",
        "score": best["score"],
        "reason": reason if best["score"] >= MATCH_THRESHOLD else f"No confident match. Closest: {', '.join(i['invoice_number'] for i in best['set'])} - {reason}",
        "amount_case": kind,
        "invoices": [i["invoice_number"] for i in best["set"]],
        "allocations": allocations,
        "suggested_action": "close_tds" if kind in ("tds", "multi_tds") else ("leave_open" if kind == "partial" else None),
        "customer_id": best["customer_id"],
        "payer_text": parsed["payer_text"],
        "linked_claims": [{"claim_id": l["claim"].get("claim_id"), "exact_ref": l["exact_ref"], "snippet": l["claim"].get("snippet"),
                           "conversation_id": l["claim"].get("conversation_id"), "customer_id": l["claim"].get("customer_id")} for l in best["links"]],
        "alternatives": [{"invoices": [i["invoice_number"] for i in c["set"]], "score": c["score"], "reason": "; ".join(c["why"][:2])}
                         for c in candidates[1:4]],
        "notes": notes,
    }
    return out


def _result(status, score, reason, parsed=None):
    return {"status": status, "score": score, "reason": reason, "amount_case": None, "invoices": [], "allocations": [],
            "suggested_action": None, "customer_id": None, "payer_text": (parsed or {}).get("payer_text", ""),
            "linked_claims": [], "alternatives": [], "notes": []}


def reconcile(txns: list[dict], customers: list[dict], invoices: list[dict], claims: list[dict] | None = None) -> list[dict]:
    """Suggest an invoice for each transaction. Higher-confidence rows claim invoice balances first so two receipts of the
    same amount are spread over different invoices instead of both pointing at one."""
    ctx = Context(customers, invoices, claims)
    first = [suggest_one(t, ctx) for t in txns]
    order = sorted(range(len(txns)), key=lambda i: -first[i]["score"])
    virtual: dict[str, Decimal] = {}
    final: list[dict | None] = [None] * len(txns)
    for i in order:
        s = suggest_one(txns[i], ctx, virtual)
        final[i] = s
        if s["status"] == "match":
            for a in s["allocations"]:
                inv = ctx.invoices[a["invoice_number"]]
                virtual[a["invoice_number"]] = _bal(inv, virtual) - D(a["amount"]) - D(a["shortfall"])
    return final  # type: ignore[return-value]
