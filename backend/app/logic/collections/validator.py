"""Validate a drafted reply against the database facts before it may be sent.

Checks: every currency amount, date and invoice number must exist in this customer's fact sheet; an invoice number must
be followed by its own amount / due date; no other customer's invoice numbers or names may appear.
"""
from __future__ import annotations

import re
from datetime import date

from ..money import parse_number, q2

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_CUR = r"(?:₹|Rs\.?\s?|INR\s?|\$|USD\s?|€|EUR\s?|£|GBP\s?)"
_NUMB = r"\d[\d,]*(?:\.\d+)?"
AMT_PREFIX = re.compile(rf"{_CUR}\s?({_NUMB})", re.I)
AMT_SUFFIX = re.compile(rf"({_NUMB})\s?(?:INR|USD|EUR|GBP|rupees)\b", re.I)
AMT_FORMATTED = re.compile(r"(?<![\w.,])(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+\.\d{1,2})(?![\w,])")
INV_RE = re.compile(r"\bINV[\s\-]?(\d{3,6})\b", re.I)
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}\.?(?:,?\s+(\d{{4}}))?\b", re.I)
DATE_MDY = re.compile(rf"\b{_MON}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", re.I)
DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DATE_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def extract_dates(text: str) -> list[tuple[int | None, int, int]]:
    """Return (year|None, month, day) tuples."""
    out = []
    for m in DATE_DMY.finditer(text):
        out.append((int(m.group(3)) if m.group(3) else None, _MONTHS[m.group(2)[:3].lower()], int(m.group(1))))
    for m in DATE_MDY.finditer(text):
        out.append((int(m.group(3)) if m.group(3) else None, _MONTHS[m.group(1)[:3].lower()], int(m.group(2))))
    for m in DATE_ISO.finditer(text):
        out.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in DATE_SLASH.finditer(text):
        out.append((int(m.group(3)), int(m.group(2)), int(m.group(1))))
    return out


def extract_amounts(text: str):
    found = []
    for rx in (AMT_PREFIX, AMT_SUFFIX):
        for m in rx.finditer(text):
            v = parse_number(m.group(1))
            if v is not None:
                found.append(q2(v))
    for m in AMT_FORMATTED.finditer(text):
        v = parse_number(m.group(1))
        if v is not None:
            found.append(q2(v))
    return found


def validate_reply(text: str, facts: dict, customers: list[dict], inbound_text: str = "") -> list[str]:
    problems: list[str] = []
    allowed_nums = facts["allowed_invoice_numbers"]
    own_id = facts["customer"]["customer_id"]

    # 1. invoice numbers
    for m in INV_RE.finditer(text):
        num = f"INV-{int(m.group(1)):04d}" if len(m.group(1)) <= 4 else f"INV-{m.group(1)}"
        if num not in allowed_nums:
            problems.append(f"invoice number {num} is not an outstanding invoice of this customer")

    # 2. amounts
    for a in set(extract_amounts(text)):
        if a not in facts["allowed_amounts"]:
            problems.append(f"amount {a} does not match any of this customer's invoices")

    # 3. dates (must be an invoice/due date, today, or a date the customer wrote in their own email)
    allowed = {(d.year, d.month, d.day) for d in facts["allowed_dates"]} | {(facts["today"].year, facts["today"].month, facts["today"].day)}
    inbound_md = {(m, d) for _, m, d in extract_dates(inbound_text)}
    for y, mth, d in extract_dates(text):
        ok = (y, mth, d) in allowed if y else any((mth, d) == (m, dd) for _, m, dd in allowed)
        if not ok and (mth, d) in inbound_md:
            ok = True  # echoing a date the customer wrote
        if not ok:
            problems.append(f"date {d}/{mth}{('/' + str(y)) if y else ''} does not match the database")

    # 4. invoice number must be followed by its own amount and due date
    for m in re.finditer(rf"INV[\s\-]?(\d{{3,6}})[^\d\n]{{0,14}}?{_CUR}\s?({_NUMB})", text, re.I):
        num = f"INV-{int(m.group(1)):04d}"
        v = q2(parse_number(m.group(2)) or 0)
        if num in facts["per_invoice_amounts"] and v not in facts["per_invoice_amounts"][num]:
            problems.append(f"{num} is paired with the wrong amount {v}")
    due = {r["invoice_number"]: r["due_raw"] for r in facts["invoices"]}
    for m in re.finditer(r"INV[\s\-]?(\d{3,6})(?:(?!INV)[^\n]){0,70}?\bdue\s+(?:on\s+|by\s+)?(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\.?\s+\d{4})", text, re.I):
        num = f"INV-{int(m.group(1)):04d}"
        ds = extract_dates(m.group(2))
        if num in due and ds and (ds[0][0], ds[0][1], ds[0][2]) != (due[num].year, due[num].month, due[num].day):
            problems.append(f"{num} is paired with the wrong due date")

    # 5. isolation: other customers' names or invoice numbers must never appear
    low = text.lower()
    for c in customers:
        if c["customer_id"] == own_id:
            continue
        brand = re.split(r"\s+", c["name"].strip())[0].lower()
        if len(brand) >= 4 and re.search(rf"\b{re.escape(brand)}\b", low):
            problems.append(f"mentions another customer ({c['name']})")
    return list(dict.fromkeys(problems))
