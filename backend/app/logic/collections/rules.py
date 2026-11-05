"""Deterministic safety net and offline classifier for customer emails.

These keyword rules run on every inbound email, even when an LLM is used: the final decision is the *union* of risky
intents, so a model that wrongly calls a legal threat "routine" is overruled.
"""
from __future__ import annotations

import re

ROUTINE = {"balance_query", "payment_instructions", "payment_claim", "tds_query"}
ESCALATE = {"extension_request", "dispute", "legal", "other_company", "unclear", "other", "unknown_sender"}

_P = {
    "legal": r"\b(legal|lawyer|advocate|attorney|solicitor|court|sue|suing|lawsuit|litigation|tribunal|consumer forum|police|fraud|harass)",
    "dispute": r"\b(disput\w*|incorrect|wrong(ly)?|overcharg\w*|not agree|do not agree|don't agree|mistake|error|not (received|delivered|used)|"
               r"never (ordered|received|agreed)|duplicate invoice|credit note|cancel(l)?ed|refuse|not (owe|liable)|already paid twice)",
    "extension_request": r"\b(extension|extend|more time|(more|extra|additional) (days?|weeks?|months?)|additional time|postpone\w*|defer\w*|instal+ments?|emi|reschedul\w*|"
                         r"(can|could|may) (we|i) pay (it )?(later|next)|(unable|cannot|can't|can not|won't be able) to pay|delay (the )?payment|"
                         r"waive|waiver|discount|settle for|reduce)",
    "payment_claim": r"\b(i|we)('ve|\s+have|\s+had)?\s*(already\s+)?(paid|made (the )?payment|transferred|remitted|sent (the )?(money|payment))|"
                     r"\b(has|have) been (paid|transferred|remitted)|payment (was|has been) (made|done|sent)|"
                     r"\bwill (pay|transfer|remit|make (the )?payment)|\bpaying (on|by)|\bpayment (will be|is being) (made|done|released)|"
                     r"\butr\b|reference (no|number)\b",
    "balance_query": r"\b(balance|outstanding|pending|how much|amount (due|pending|owed)|statement|invoices?|what (do|did) (i|we) owe|total due|due date|overdue)\b",
    "payment_instructions": r"\b(bank details|account (number|details|no)|ifsc|swift|iban|how (do|can|should) (i|we) pay|where (do|can|should) (i|we) pay|"
                            r"payment (details|instructions|methods?|options?)|pay (by|via|through)|neft|rtgs|imps|upi|beneficiary)",
    "tds_query": r"\b(tds|tax deducted|withholding|form 16a|26as|tds certificate)",
}
_RX = {k: re.compile(v, re.I) for k, v in _P.items()}


def detect(text: str) -> set[str]:
    return {k for k, rx in _RX.items() if rx.search(text or "")}


def risky_flags(text: str) -> set[str]:
    return detect(text) & {"legal", "dispute", "extension_request"}


def offline_classify(text: str) -> dict:
    """Keyword classifier used when no LLM is configured (LLM_PROVIDER=none)."""
    found = detect(text)
    intents = sorted(found) or ["unclear"]
    conf = 0.9 if found else 0.3
    return {"intents": intents, "confidence": conf, "summary": "Keyword classification (no LLM configured).", "payment_claim": None, "reply": None}
