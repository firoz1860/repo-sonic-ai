"""Invoice domain rules: overdue, outstanding, TDS."""
from __future__ import annotations

from datetime import date

from .money import D, parse_date, q2

TDS_RATE = D("0.10")


def is_outstanding(inv: dict) -> bool:
    """Customer owes money: sent (not draft/paid) and has a balance."""
    return inv.get("status") == "sent" and D(inv.get("balance_due")) > 0


def is_overdue(inv: dict, today: date) -> bool:
    return is_outstanding(inv) and parse_date(inv["due_date"]) < today


def days_overdue(inv: dict, today: date) -> int:
    return max(0, (today - parse_date(inv["due_date"])).days) if is_outstanding(inv) else 0


def tds_applicable(inv: dict) -> bool:
    return str(inv.get("tax_name", "")).upper().startswith("GST") and inv.get("currency") == "INR"


def tds_amount(inv: dict):
    """TDS = 10% of the invoice subtotal (assignment definition). Only for Indian (GST) invoices."""
    return q2(D(inv["subtotal"]) * TDS_RATE) if tds_applicable(inv) else D(0)


def tds_variants(inv: dict) -> list:
    """Accepted TDS amounts: 10% of subtotal (primary) and 10% of discounted subtotal."""
    if not tds_applicable(inv):
        return []
    a = tds_amount(inv)
    b = q2((D(inv["subtotal"]) - D(inv.get("discount"))) * TDS_RATE)
    return [a] if a == b else [a, b]


def decorate(inv: dict, today: date) -> dict:
    out = {k: v for k, v in inv.items() if k != "_id"}
    out["is_overdue"] = is_overdue(inv, today)
    out["days_overdue"] = days_overdue(inv, today)
    out["unpaid_amount"] = float(q2(inv.get("balance_due", 0))) if inv.get("status") == "sent" else 0.0
    out["tds_amount"] = float(tds_amount(inv))
    return out
