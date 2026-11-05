"""Parse a free-text bank narration into structured hints (method, invoice refs, PO refs, payer text)."""
from __future__ import annotations

import re

METHODS = ("NEFT", "RTGS", "IMPS", "UPI", "SWIFT", "SEPA", "ACH", "CHQ", "CHEQUE", "CASH", "WIRE", "FT")
# words that describe the payment rather than naming the payer
MEMO_WORDS = {
    "CR", "DR", "PART", "PARTIAL", "PAYMENT", "PAYMENTS", "PMT", "PYMT", "PAYT", "DUES", "DUE", "BALANCE", "BAL",
    "FOR", "TO", "FROM", "BEING", "REF", "INV", "INVOICE", "INVOICES", "RE", "BILL", "ADVANCE", "SETTLEMENT",
    "TRANSFER", "TXN", "TRF", "BY", "OF", "AGAINST", "AGST", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL",
    "AUG", "SEP", "SEPT", "OCT", "NOV", "DEC", "PO", "SERVICES-FEE",
}
NON_CUSTOMER_PATTERNS = re.compile(
    r"\b(INT\.?\s?PD|INTEREST|INT\s?CR|CHARGES?|CHRG|BANK\s?CHG|SERVICE\s?TAX|GST\s+ON|REVERSAL|REFUND|"
    r"CASHBACK|SALARY|RENT|VENDOR|DIVIDEND)\b",
    re.I,
)
_INV_RE = re.compile(r"\bINV(?:OICE)?[\s\-_./#:]*0*(\d{3,6})((?:\s*(?:,|&|\+|/|AND)\s*0*\d{3,6})*)", re.I)
_RE_RE = re.compile(r"\b(?:RE|REF|BILL)[\s\-_./#:]*0*(\d{4,6})\b", re.I)


def _alnum(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def parse_narration(narration: str, known_invoice_numbers: set[str], known_pos: set[str]) -> dict:
    text = (narration or "").upper()
    method = next((m for m in METHODS if re.search(rf"\b{m}\b", text)), None)
    direction = "credit" if re.search(r"\bCR\b|/CR/|\bCREDIT\b", text) else ("debit" if re.search(r"\bDR\b|\bDEBIT\b", text) else None)

    refs: list[str] = []
    unknown_refs: list[str] = []
    spans: list[tuple[int, int]] = []

    def add_ref(num: str):
        inv = f"INV-{int(num):04d}" if len(num) <= 4 else f"INV-{num}"
        (refs if inv in known_invoice_numbers else unknown_refs).append(inv)

    for m in _INV_RE.finditer(text):
        add_ref(m.group(1))
        for extra in re.findall(r"\d{3,6}", m.group(2) or ""):
            add_ref(extra)
        spans.append(m.span())
    for m in _RE_RE.finditer(text):
        inv = f"INV-{int(m.group(1)):04d}"
        if inv in known_invoice_numbers:
            if inv not in refs:
                refs.append(inv)
            spans.append(m.span())
    refs = list(dict.fromkeys(refs))
    unknown_refs = [u for u in dict.fromkeys(unknown_refs) if u not in refs]

    alnum_text = _alnum(text)
    po_refs = [po for po in known_pos if po and _alnum(po) in alnum_text]

    # payer text = narration minus method, refs, reference-like tokens and memo words
    chars = list(text)
    for a, b in spans:
        for i in range(a, b):
            chars[i] = " "
    cleaned = "".join(chars)
    for po in po_refs:
        cleaned = re.sub(r"[\s\-]*".join(map(re.escape, po)), " ", cleaned)
    cleaned = re.sub(r"\bINT\.?\s?PD:?[\d\- ]*TO[\d\- ]*", " ", cleaned)
    cleaned = re.sub(r"[/\-_:.,;()]", " ", cleaned)
    tokens = []
    for t in cleaned.split():
        if t in METHODS or t in MEMO_WORDS:
            continue
        if any(c.isdigit() for c in t):
            continue
        tokens.append(t)
    return {
        "method": method,
        "direction": direction,
        "invoice_refs": refs,
        "unknown_refs": unknown_refs,
        "po_refs": po_refs,
        "payer_text": " ".join(tokens),
        "part_payment": bool(re.search(r"\bPART(IAL)?\b|\bINSTAL", text)),
        "non_customer": bool(NON_CUSTOMER_PATTERNS.search(text)),
    }
