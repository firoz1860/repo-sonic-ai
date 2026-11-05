"""Money and date helpers. All arithmetic uses Decimal; amounts are in major units."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}
CURRENCIES = tuple(SYMBOLS)
TWO = Decimal("0.01")


def D(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def q2(value) -> Decimal:
    return D(value).quantize(TWO, rounding=ROUND_HALF_UP)


def to_float(value) -> float:
    return float(q2(value))


def _group_indian(int_part: str) -> str:
    if len(int_part) <= 3:
        return int_part
    head, tail = int_part[:-3], int_part[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def format_amount(amount, currency: str) -> str:
    """₹1,18,000.00 for INR (lakh grouping), $12,500.00 / €9,200.00 / £7,350.50 otherwise."""
    value = q2(amount)
    sign = "-" if value < 0 else ""
    whole, frac = f"{abs(value):.2f}".split(".")
    grouped = _group_indian(whole) if currency == "INR" else f"{int(whole):,}"
    return f"{sign}{SYMBOLS.get(currency, currency + ' ')}{grouped}.{frac}"


def parse_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def format_date(value) -> str:
    d = parse_date(value)
    return f"{d.day} {d.strftime('%B %Y')}"


_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def parse_number(text) -> Decimal | None:
    """Parse '2,36,000.00', '59000', '7,350.5' (also '₹ 1,18,000') into Decimal."""
    if text is None:
        return None
    m = _NUM.search(str(text))
    if not m:
        return None
    try:
        return Decimal(m.group(0).replace(",", ""))
    except Exception:  # noqa: BLE001
        return None
