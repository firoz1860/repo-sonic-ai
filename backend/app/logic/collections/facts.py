"""Build the per-customer fact sheet. This is the ONLY customer data an LLM or template ever sees."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from ..invoices import days_overdue, is_outstanding, is_overdue, tds_amount, tds_applicable
from ..money import D, format_amount, format_date, parse_date, q2


def first_name(contact: str) -> str:
    parts = [p for p in (contact or "").replace(".", ". ").split() if p]
    if parts and parts[0].lower() in {"dr.", "mr.", "ms.", "mrs.", "prof."} and len(parts) > 1:
        return f"{parts[0]} {parts[1]}"
    return parts[0] if parts else "there"


def build_facts(org: dict, customer: dict, invoices: list[dict], today: date) -> dict:
    """Only this customer's sent, unpaid invoices are included (drafts and paid invoices are excluded)."""
    mine = [i for i in invoices if i["customer_id"] == customer["customer_id"] and is_outstanding(i)]
    mine.sort(key=lambda i: (i["due_date"], i["invoice_number"]))
    rows, allowed_amounts, per_invoice_amounts, dates = [], set(), {}, set()
    totals: dict[str, Decimal] = {}
    overdue_totals: dict[str, Decimal] = {}
    for inv in mine:
        cur = inv["currency"]
        bal = q2(inv["balance_due"])
        tds = tds_amount(inv) if tds_applicable(inv) else None
        amounts = {q2(inv["total"]), bal, q2(inv["subtotal"]), q2(inv.get("amount_paid", 0))}
        if tds:
            amounts |= {tds, q2(bal - tds)}
        per_invoice_amounts[inv["invoice_number"]] = {a for a in amounts if a > 0}
        allowed_amounts |= per_invoice_amounts[inv["invoice_number"]]
        dates |= {parse_date(inv["invoice_date"]), parse_date(inv["due_date"])}
        totals[cur] = totals.get(cur, Decimal(0)) + bal
        if is_overdue(inv, today):
            overdue_totals[cur] = overdue_totals.get(cur, Decimal(0)) + bal
        rows.append({
            "invoice_number": inv["invoice_number"], "po_number": inv.get("po_number"), "currency": cur,
            "invoice_date": format_date(inv["invoice_date"]), "due_date": format_date(inv["due_date"]),
            "total": format_amount(inv["total"], cur), "amount_paid": format_amount(inv.get("amount_paid", 0), cur),
            "balance_due": format_amount(bal, cur), "balance_raw": bal, "due_raw": parse_date(inv["due_date"]),
            "overdue": is_overdue(inv, today), "days_overdue": days_overdue(inv, today),
            "tds_amount": format_amount(tds, cur) if tds else None,
            "tds_net_payable": format_amount(bal - tds, cur) if tds else None,
        })
    for cur, t in list(totals.items()) + list(overdue_totals.items()):
        allowed_amounts.add(q2(t))
    wh = org.get("policies", {}).get("working_hours", "")
    return {
        "today": today, "today_text": format_date(today),
        "org": {"name": org["name"], "accounts_contact_name": org.get("accounts_contact_name", "Accounts Team"),
                "accounts_email": org.get("accounts_email"), "phone": org.get("phone"), "address": org.get("address"),
                "payment_methods": org.get("payment_methods", []), "bank_details": org.get("bank_details", {}),
                "policies": org.get("policies", {}), "payment_terms": org.get("payment_terms"), "working_hours": wh,
                "default_currency": org.get("default_currency")},
        "customer": {"customer_id": customer["customer_id"], "name": customer["name"], "contact_name": customer["contact_name"],
                     "currency": customer["currency"], "country": customer.get("country")},
        "invoices": rows,
        "totals": {c: format_amount(t, c) for c, t in totals.items()},
        "overdue_totals": {c: format_amount(t, c) for c, t in overdue_totals.items()},
        "allowed_amounts": allowed_amounts, "per_invoice_amounts": per_invoice_amounts,
        "allowed_dates": dates, "allowed_invoice_numbers": {r["invoice_number"] for r in rows},
    }


def llm_view(facts: dict) -> dict:
    """JSON-safe subset sent to the LLM."""
    keep = ("invoice_number", "po_number", "invoice_date", "due_date", "total", "amount_paid", "balance_due",
            "overdue", "days_overdue", "tds_amount", "tds_net_payable")
    return {
        "today": facts["today_text"], "organization": facts["org"], "customer": facts["customer"],
        "outstanding_invoices": [{k: r[k] for k in keep} for r in facts["invoices"]],
        "total_outstanding": facts["totals"], "total_overdue": facts["overdue_totals"],
    }
