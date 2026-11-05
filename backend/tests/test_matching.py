"""Bank reconciliation: results on the sample statement and tricky variations."""
import csv
import json

from app.logic.matching.engine import reconcile
from app.logic.matching.normalize import parse_narration
from app.logic.matching.similarity import payer_similarity
from conftest import DATA

CUST = json.load(open(DATA / "customers.json"))
INV = json.load(open(DATA / "invoices.json"))


def rows():
    out = []
    for r in csv.DictReader(open(DATA / "bank_statement_sample.csv")):
        out.append({"txn_date": r["txn_date"], "narration": r["narration"], "reference": r["reference"], "credit": r["credit"] or 0, "debit": r["debit"] or 0, "currency": r["currency"]})
    return out


def one(narr, amount, cur="INR", date="2026-10-20", ref="", invs=None, claims=None):
    t = {"txn_date": date, "narration": narr, "reference": ref, "credit": amount, "debit": 0, "currency": cur}
    return reconcile([t], CUST, invs or INV, claims)[0]


def test_sample_statement_expected_matches():
    res = reconcile(rows(), CUST, INV)
    got = [(r["status"], r["invoices"]) for r in res]
    assert got == [
        ("match", ["INV-1004"]),                       # Kestrel, invoice number in narration (INV-1005 has same amount)
        ("match", ["INV-1013"]),                       # Orbis part payment
        ("match", ["INV-1007"]),                       # Quillon paid net of 10% TDS
        ("match", ["INV-1024"]),                       # Nordlys: not INV-1025 (dated after the payment)
        ("match", ["INV-1021"]),
        ("match", ["INV-1002"]),
        ("match", ["INV-1016"]),                       # Redwood
        ("ignored", []),                               # debit
        ("match", ["INV-1026"]),
        ("match", ["INV-1010", "INV-1011", "INV-1012"]),  # one payment, three invoices
        ("ignored", []),
        ("no_match", []),                              # R K Sharma: payer unknown, amount alone is not enough
        ("no_match", []),                              # Maple Retail: unknown payer
        ("no_match", []),                              # bank interest
    ]
    assert res[2]["amount_case"] == "tds" and res[2]["suggested_action"] == "close_tds"
    assert res[1]["amount_case"] == "partial" and 50 <= res[1]["score"] < 85
    assert all(r["score"] >= 85 for i, r in enumerate(res) if i in (0, 3, 4, 5))


def test_no_wrong_match_for_unknown_payer_even_with_exact_amount():
    r = one("IMPS/R K SHARMA/CLINIC DUES", 88500)
    assert r["status"] == "no_match"


def test_same_amount_two_invoices_uses_narration_then_fifo():
    assert one("IMPS/KESTREL FOODS/INV 1005", 59000)["invoices"] == ["INV-1005"]
    r = one("IMPS/KESTREL FOODS", 59000)
    assert r["invoices"] == ["INV-1004"]  # earliest due date first
    assert r["score"] < one("IMPS/KESTREL FOODS/INV 1004", 59000)["score"]


def test_two_receipts_same_amount_go_to_different_invoices():
    t = [{"txn_date": "2026-10-20", "narration": "IMPS/KESTREL FOODS", "reference": f"R{i}", "credit": 59000, "debit": 0, "currency": "INR"} for i in (1, 2)]
    res = reconcile(t, CUST, INV)
    assert sorted(r["invoices"][0] for r in res) == ["INV-1004", "INV-1005"]


def test_typos_and_abbreviations():
    assert one("NEFT CR-BRIGHTLNE LOGISTIC PVT", 236000)["invoices"] == ["INV-1002"]
    assert one("NEFT CR-KESTREL FODS PVT LTD", 59000)["status"] == "match"
    p, _ = payer_similarity("ORBIS PKG", "Orbis Packaging Ltd")
    assert p > 0.75
    p2, _ = payer_similarity("R K SHARMA CLINIC", "Redwood Clinics Pvt Ltd")
    assert p2 < 0.35


def test_currency_must_match():
    assert one("NEFT CR-MAPLE RETAIL", 9200, cur="INR")["status"] == "no_match"
    assert one("SEPA CR NORDLYS RETAIL GMBH", 9200, cur="EUR", date="2026-10-20")["invoices"] == ["INV-1024"]


def test_draft_and_paid_invoices_never_suggested():
    r = one("NEFT CR-KESTREL FOODS INV-1006", 147500)  # draft
    assert r["status"] == "no_match"
    r = one("NEFT CR-BRIGHTLINE LOGISTICS INV-1001", 118000)  # already paid -> possible duplicate
    assert r["status"] == "no_match" and "already paid" in r["reason"]


def test_payment_before_invoice_date_is_not_a_confident_match():
    r = one("NEFT CR-ORBIS PACKAGING INV-1014", 129800, date="2026-10-20")  # INV-1014 is dated 2026-11-01
    assert r["score"] < 50


def test_po_number_is_evidence():
    r = one("NEFT CR-UNKNOWN TRADING BL-4520", 236000)
    assert r["invoices"] == ["INV-1002"]


def test_tds_two_invoices_and_discount_variant():
    # INV-1012 has a discount: 10% of subtotal = 6500, of discounted subtotal = 6000. Both accepted.
    assert one("NEFT CR-SAFFRON PEAK HOSPITALITY INV-1012", 64300)["amount_case"] == "tds"
    assert one("NEFT CR-SAFFRON PEAK HOSPITALITY INV-1012", 64800)["amount_case"] == "tds"


def test_foreign_small_shortfall_is_partial_with_bank_charge_note():
    r = one("SWIFT CR HARBOUR ANALYTICS INC INV1021", 12470, cur="USD", date="2026-10-01")
    assert r["amount_case"] == "partial" and "bank charges" in r["reason"]


def test_overpayment_is_not_auto_matched():
    assert one("NEFT CR-BRIGHTLINE LOGISTICS INV-1002", 300000)["status"] == "no_match"


def test_email_reference_links_and_boosts():
    claims = [{"claim_id": "c1", "customer_id": "C006", "invoice_numbers": ["INV-1017"], "reference": "629315881020", "claimed_date": "2026-10-19", "snippet": "paid", "conversation_id": "x"}]
    r = one("IMPS/R K SHARMA/CLINIC DUES", 88500, date="2026-10-20", ref="629315881020", claims=claims)
    assert r["status"] == "match" and r["invoices"] == ["INV-1017"] and r["linked_claims"][0]["exact_ref"]


def test_narration_parser():
    p = parse_narration("SWIFT CR HARBOUR ANALYTICS INC INV1021", {"INV-1021"}, set())
    assert p["invoice_refs"] == ["INV-1021"] and p["method"] == "SWIFT" and "INV" not in p["payer_text"]
    p = parse_narration("NEFT CR-X-INV-1002, 1003", {"INV-1002", "INV-1003"}, set())
    assert p["invoice_refs"] == ["INV-1002", "INV-1003"]
