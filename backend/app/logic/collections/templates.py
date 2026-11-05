"""Deterministic email templates. Every figure comes straight from the fact sheet, so these are correct by construction."""
from __future__ import annotations

from ..money import format_amount, format_date
from .facts import first_name

SIGNOFF = "Kind regards,\n{contact}\n{org}\n{phone}"


def signoff(facts: dict) -> str:
    o = facts["org"]
    return SIGNOFF.format(contact=o["accounts_contact_name"], org=o["name"], phone=o.get("phone") or "").rstrip()


def invoice_line(r: dict, with_overdue: bool = False) -> str:
    line = f"{r['invoice_number']} for {r['balance_due']}, due {r['due_date']}"
    if r["amount_paid"] and r["amount_paid"] != format_amount(0, r["currency"]):
        line += f" (invoice total {r['total']}, {r['amount_paid']} already received)"
    if with_overdue and r["overdue"]:
        line += f" - {r['days_overdue']} day{'s' if r['days_overdue'] != 1 else ''} overdue"
    return line


def payment_block(facts: dict) -> str:
    o, b = facts["org"], facts["org"]["bank_details"]
    foreign = facts["customer"]["currency"] != "INR"
    methods = [m for m in o["payment_methods"] if (m in ("SWIFT", "SEPA") if foreign else m not in ("SWIFT", "SEPA"))]
    text = f"You can pay by {', '.join(methods[:-1]) + ' or ' + methods[-1] if len(methods) > 1 else ''.join(methods)} to {b.get('account_name')}, account {b.get('account_number')}, {b.get('bank')}"
    text += f", SWIFT code {b.get('swift_code')}." if foreign and b.get("swift_code") else f", IFSC {b.get('ifsc')}."
    if foreign and o["policies"].get("bank_charges"):
        text += " " + o["policies"]["bank_charges"]
    return text


def reminder_email(facts: dict, rows: list[dict]) -> tuple[str, str]:
    nums = ", ".join(r["invoice_number"] for r in rows)
    subject = f"Payment reminder: overdue invoice{'s' if len(rows) > 1 else ''} {nums} - {facts['org']['name']}"
    lines = [f"Dear {first_name(facts['customer']['contact_name'])},", "",
             f"This is a friendly reminder that the following invoice{'s are' if len(rows) > 1 else ' is'} past due as of {facts['today_text']}:", ""]
    lines += [f"  - {invoice_line(r, True)}" for r in rows]
    cur_totals: dict[str, float] = {}
    for r in rows:
        cur_totals.setdefault(r["currency"], 0)
    if len(rows) > 1:
        from decimal import Decimal
        for cur in cur_totals:
            tot = sum((r["balance_raw"] for r in rows if r["currency"] == cur), Decimal(0))
            lines += ["", f"Total overdue: {format_amount(tot, cur)}"]
    lines += ["", payment_block(facts)]
    if facts["customer"]["currency"] == "INR" and facts["org"]["policies"].get("tds"):
        lines += ["", facts["org"]["policies"]["tds"]]
    lines += ["", "If you have already made this payment, please reply with the transaction reference and we will reconcile it.", "", signoff(facts)]
    return subject, "\n".join(lines)


def routine_reply(facts: dict, intents: set[str], claim: dict | None = None) -> str:
    """Compose a complete reply for routine intents from verified facts only."""
    c = facts["customer"]
    out = [f"Dear {first_name(c['contact_name'])},", ""]
    rows = facts["invoices"]
    wants_list = bool(intents & {"balance_query", "payment_instructions"}) or not intents - {"payment_claim", "tds_query"}
    if claim is not None or "payment_claim" in intents:
        nums = [n for n in (claim or {}).get("invoice_numbers", []) if n in facts["allowed_invoice_numbers"]]
        what = f"for {', '.join(nums)} " if nums else ""
        when = ""
        if (claim or {}).get("date"):
            try:
                when = f" on {format_date(claim['date'])}"
            except ValueError:
                when = ""
        out += [f"Thank you for letting us know about your payment {what}{when}".replace("  ", " ").rstrip() + ".",
                "We have noted this, but we can only update the invoice once the amount is confirmed in our bank account; "
                "our Accounts Team will do this after reconciliation. If you have a transaction reference, please share it.", ""]
    if wants_list:
        if rows:
            n = len(rows)
            out.append(f"{'Your outstanding invoice is' if n == 1 else f'You have {n} outstanding invoices'} as of {facts['today_text']}:")
            out += [f"  - {invoice_line(r, True)}" for r in rows]
            for cur, t in facts["totals"].items():
                out.append(f"Total outstanding: {t}" if n > 1 else "")
            out = [l for l in out if l != "" or True]
            out.append("")
        else:
            out += ["We have no outstanding invoices on your account at the moment.", ""]
        out += [payment_block(facts), ""]
    if "tds_query" in intents:
        pol = facts["org"]["policies"].get("tds", "")
        out.append(pol)
        tds_rows = [r for r in rows if r["tds_amount"]]
        for r in tds_rows:
            out.append(f"  - {r['invoice_number']}: TDS {r['tds_amount']}, so the amount payable net of TDS is {r['tds_net_payable']}.")
        out.append("")
    out += [f"Our team is available {facts['org']['working_hours']}.", "", signoff(facts)]
    text = "\n".join(out)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text


def holding_reply(facts: dict | None, org: dict, contact_name: str | None) -> str:
    """Safe acknowledgement for a human to edit; it promises nothing."""
    name = first_name(contact_name) if contact_name else "there"
    wh = org.get("policies", {}).get("working_hours", "")
    return (f"Dear {name},\n\nThank you for your message. We have passed it to our Accounts Team, who will review it and reply to you"
            f"{' during our working hours (' + wh + ')' if wh else ''}.\n\nKind regards,\n{org.get('accounts_contact_name', 'Accounts Team')}\n{org['name']}")
