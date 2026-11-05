"""HTTP API: auth, base data, upload -> approve -> invoice + agent update, reset, security."""
from pathlib import Path

from conftest import DATA, raw_email

SAMPLE = (DATA / "bank_statement_sample.csv").read_bytes()


def upload(client, content=SAMPLE, name="s.csv"):
    r = client.post("/api/reconciliation/statements", files={"file": (name, content, "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def by_ref(st):
    return {t["reference"]: t for t in st["transactions"]}


# ---------------------------------------------------------------- auth & base data
def test_all_data_routes_require_login(ctx, settings):
    from fastapi.testclient import TestClient
    from app.main import create_app

    c = TestClient(create_app(settings, ctx))
    for path in ("/api/organization", "/api/customers", "/api/invoices", "/api/collections/conversations", "/api/reconciliation/statements"):
        assert c.get(path).status_code == 401
    assert c.get("/api/health").status_code == 200
    assert c.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    assert c.get("/api/customers", headers={"Authorization": "Bearer abc.def"}).status_code == 401


def test_base_data_overdue_and_unpaid(client):
    inv = {i["invoice_number"]: i for i in client.get("/api/invoices").json()}
    assert len(inv) == 27
    assert inv["INV-1004"]["is_overdue"] and inv["INV-1004"]["unpaid_amount"] == 59000.0 and inv["INV-1004"]["days_overdue"] == 47
    assert not inv["INV-1006"]["is_overdue"] and inv["INV-1006"]["unpaid_amount"] == 0  # draft: not owed
    assert not inv["INV-1001"]["is_overdue"]                                          # paid
    assert not inv["INV-1025"]["is_overdue"] and not inv["INV-1003"]["is_overdue"]    # not due yet
    assert inv["INV-1023"]["unpaid_amount"] == 4000.0 and inv["INV-1023"]["is_overdue"]  # part-paid
    assert sum(i["is_overdue"] for i in inv.values()) == 16
    s = client.get("/api/summary").json()
    assert s["overdue_count"] == 16 and set(s["unpaid"]) == {"INR", "USD", "EUR", "GBP"}
    assert client.get("/api/organization").json()["bank_details"]["account_number"] == "000111222333"
    assert len(client.get("/api/customers").json()) == 9


def test_edit_customer_email_validation(client):
    ok = client.patch("/api/customers/C002", json={"contact_email": "me@inbox.test"})
    assert ok.status_code == 200 and ok.json()["contact_email"] == "me@inbox.test"
    assert client.patch("/api/customers/C002", json={"contact_email": "not-an-email"}).status_code == 422
    assert client.patch("/api/customers/C002", json={"contact_email": "a@b.test\r\nBcc: x@y.test"}).status_code == 422
    assert client.patch("/api/customers/C003", json={"contact_email": "ME@inbox.test"}).status_code == 409  # duplicate across customers
    assert client.patch("/api/customers/C003", json={"contact_email": "accounts@acme-test.dev"}).status_code == 422
    assert client.patch("/api/customers/NOPE", json={"contact_email": "z@z.test"}).status_code == 404


def test_mongo_operator_injection_is_rejected(client):
    r = client.get("/api/invoices", params={"customer_id": "C001"})
    assert r.status_code == 200
    assert client.patch("/api/customers/C001", json={"contact_email": {"$ne": ""}}).status_code == 422
    assert client.post("/api/auth/login", json={"password": {"$ne": ""}}).status_code == 422


# ---------------------------------------------------------------- reconciliation
def test_upload_sample_shows_expected_suggestions(client):
    st = upload(client)
    t = by_ref(st)
    assert len(st["transactions"]) == 14
    assert t["624811204417"]["suggestion"]["invoices"] == ["INV-1004"]
    assert t["N273260011842"]["suggestion"]["score"] >= 95
    assert t["R26426009914"]["suggestion"]["suggested_action"] == "close_tds"
    assert t["N285260007120"]["suggestion"]["invoices"] == ["INV-1010", "INV-1011", "INV-1012"]
    assert t["N297260006677"]["suggestion"]["status"] == "no_match"
    # nothing is applied before a person approves
    assert {i["invoice_number"]: i["balance_due"] for i in client.get("/api/invoices").json()}["INV-1004"] == 59000.0
    assert client.get("/api/reconciliation/payments").json() == []


def test_bad_uploads_rejected(client):
    assert client.post("/api/reconciliation/statements", files={"file": ("x.txt", b"a", "text/plain")}).status_code == 415
    assert client.post("/api/reconciliation/statements", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")}).status_code == 400
    big = b"txn_date,narration,reference,credit,debit,currency\n" + b"2026-10-01,X,R,1,,INR\n" * 60000
    assert client.post("/api/reconciliation/statements", files={"file": ("x.csv", big, "text/csv")}).status_code in (400, 413)
    st = upload(client, b"txn_date,narration,reference,credit,debit,currency\n2026-13-45,NEFT CR-X,R1,abc,,INR\n2026-10-01,NEFT CR-Y,R2,5,,JPY\n")
    assert [t["status"] for t in st["transactions"]] == ["invalid", "invalid"]


def test_approve_exact_payment_marks_paid_and_stops_chasing(client):
    ctx = client.ctx
    ctx.store.set_customer_email("C001", "c1@cust.test")
    t = by_ref(upload(client))["N273260011842"]
    r = client.post(f"/api/reconciliation/transactions/{t['txn_id']}/approve", json={})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    inv = client.get("/api/invoices/INV-1002").json()
    assert inv["status"] == "paid" and inv["balance_due"] == 0 and inv["amount_paid"] == 236000.0 and not inv["is_overdue"]
    assert inv["payments"][0]["amount"] == 236000.0
    # agent no longer chases it and the paid invoice disappears from customer context
    assert all("INV-1002" not in p["invoices"] for p in ctx.agent.plan_reminders("C001"))
    # second approval of the same row is refused (no double-apply)
    assert client.post(f"/api/reconciliation/transactions/{t['txn_id']}/approve", json={}).status_code == 409


def test_tds_partial_and_multi_invoice_flows(client):
    st = upload(client)
    t = by_ref(st)
    url = lambda x: f"/api/reconciliation/transactions/{x['txn_id']}"
    # shortfall requires an explicit person choice
    r = client.post(url(t["R26426009914"]) + "/approve", json={"allocations": [{"invoice_number": "INV-1007", "amount": 324000}]})
    assert r.status_code == 422 and "TDS" in r.json()["detail"]
    r = client.post(url(t["R26426009914"]) + "/approve", json={})  # default = suggestion (close_tds)
    assert r.status_code == 200
    inv = client.get("/api/invoices/INV-1007").json()
    assert inv["status"] == "paid" and inv["balance_due"] == 0 and inv["tds_deducted"] == 30000.0 and inv["amount_paid"] == 324000.0
    # partial: leave balance open
    r = client.post(url(t["625314770213"]) + "/approve", json={"allocations": [{"invoice_number": "INV-1013", "amount": 200000, "action": "leave_open"}]})
    assert r.status_code == 200
    inv = client.get("/api/invoices/INV-1013").json()
    assert inv["status"] == "sent" and inv["balance_due"] == 272000.0 and inv["amount_paid"] == 200000.0
    # one payment, three invoices
    assert client.post(url(t["N285260007120"]) + "/approve", json={}).status_code == 200
    for n in ("INV-1010", "INV-1011", "INV-1012"):
        assert client.get(f"/api/invoices/{n}").json()["status"] == "paid"
    assert len(client.get("/api/reconciliation/payments").json()) == 5


def test_approval_guards(client):
    t = by_ref(upload(client))
    url = lambda ref: f"/api/reconciliation/transactions/{t[ref]['txn_id']}"
    post = lambda ref, body: client.post(url(ref) + "/approve", json=body)
    assert post("N297260006677", {}).status_code == 422                                   # no suggestion, nothing chosen
    assert post("N297260006677", {"allocations": [{"invoice_number": "INV-9999"}]}).status_code == 422
    assert post("N297260006677", {"allocations": [{"invoice_number": "INV-1024"}]}).status_code == 422  # EUR invoice vs INR receipt
    assert post("N297260006677", {"allocations": [{"invoice_number": "INV-1001"}]}).status_code == 422  # already paid
    assert post("N297260006677", {"allocations": [{"invoice_number": "INV-1006"}]}).status_code == 422  # draft
    assert post("N275260001365", {"allocations": [{"invoice_number": "INV-1017"}]}).status_code == 422  # 59,000 vs 88,500 -> shortfall w/o action
    assert post("N275260001365", {"allocations": [{"invoice_number": "INV-1018"}]}).status_code == 422  # 59,000 > 35,400 overpayment
    assert post("N275260001365", {"allocations": [{"invoice_number": "INV-1016", "amount": 1000, "action": "leave_open"}]}).status_code == 422  # != receipt
    assert client.get("/api/invoices/INV-1016").json()["balance_due"] == 59000.0           # nothing leaked through failed approvals
    # manual override: person picks a different invoice for a receipt
    r = post("629315881020", {"allocations": [{"invoice_number": "INV-1017"}]})
    assert r.status_code == 200 and client.get("/api/invoices/INV-1017").json()["status"] == "paid"


def test_reject_reopen_and_duplicate_statement_upload(client):
    t = by_ref(upload(client))["624811204417"]
    assert client.post(f"/api/reconciliation/transactions/{t['txn_id']}/reject").json()["status"] == "rejected"
    assert client.post(f"/api/reconciliation/transactions/{t['txn_id']}/approve", json={}).status_code == 409
    assert client.post(f"/api/reconciliation/transactions/{t['txn_id']}/reopen").json()["status"] == "pending"
    assert client.post(f"/api/reconciliation/transactions/{t['txn_id']}/approve", json={}).status_code == 200
    again = by_ref(upload(client))["624811204417"]  # same statement uploaded twice
    assert again["suggestion"]["status"] == "no_match" and "duplicate" in again["suggestion"]["reason"].lower()
    assert client.get("/api/invoices/INV-1004").json()["balance_due"] == 0


def test_balances_change_between_rows_two_receipts_same_amount(client):
    csv = (b"txn_date,narration,reference,credit,debit,currency\n2026-10-20,IMPS/KESTREL FOODS,A1,59000,,INR\n2026-10-21,IMPS/KESTREL FOODS,A2,59000,,INR\n")
    t = by_ref(upload(client, csv))
    for ref in ("A1", "A2"):
        assert client.post(f"/api/reconciliation/transactions/{t[ref]['txn_id']}/approve", json={}).status_code == 200
    assert client.get("/api/invoices/INV-1004").json()["status"] == "paid" and client.get("/api/invoices/INV-1005").json()["status"] == "paid"


def test_email_claim_links_to_bank_row(client):
    ctx = client.ctx
    ctx.store.set_customer_email("C006", "c6@cust.test")
    from app.logic.collections.parser import parse_raw_email

    ctx.agent.process_email(parse_raw_email(raw_email("c6@cust.test", "Paid", "We paid INV-1017 on 19 Oct, reference 629315881020. Regards, R K Sharma")))
    row = by_ref(upload(client))["629315881020"]
    s = row["suggestion"]
    assert s["status"] == "match" and s["invoices"] == ["INV-1017"] and s["linked_claims"][0]["exact_ref"]
    assert "email" in s["reason"]


def test_reset_restores_data_and_clears_everything(client):
    ctx = client.ctx
    ctx.store.set_customer_email("C002", "mine@inbox.test")
    t = by_ref(upload(client))["624811204417"]
    client.post(f"/api/reconciliation/transactions/{t['txn_id']}/approve", json={})
    ctx.agent.send_reminders("C002")
    assert client.post("/api/admin/reset", json={"keep_emails": True}).status_code == 200
    assert client.get("/api/customers/C002").json()["contact_email"] == "mine@inbox.test"
    inv = {i["invoice_number"]: i for i in client.get("/api/invoices").json()}
    assert inv["INV-1004"]["balance_due"] == 59000.0 and inv["INV-1004"]["status"] == "sent"
    assert client.get("/api/reconciliation/statements").json() == [] and client.get("/api/reconciliation/payments").json() == []
    assert client.get("/api/collections/conversations").json() == []
    client.post("/api/admin/reset", json={})
    assert client.get("/api/customers/C002").json()["contact_email"] == "customer2@example.com"
    assert client.get("/api/organization").json()["accounts_email"] == "accounts@acme-test.dev"


def test_collections_endpoints_roundtrip(client):
    ctx = client.ctx
    for c in ctx.store.customers():
        ctx.store.set_customer_email(c["customer_id"], f"c{c['customer_id'][-1]}@cust.test")
    r = client.post("/api/collections/reminders/send", json={"customer_id": "C002", "dry_run": True}).json()["results"]
    assert r[0]["status"] == "preview" and ctx.mail.sent == []
    assert client.post("/api/collections/reminders/send", json={"customer_id": "C002"}).json()["results"][0]["status"] == "sent"
    ctx.mail.inbox.append(raw_email("c2@cust.test", "Re: reminder", "Can you give us more time?", msg_id="<q1@x>"))
    res = client.post("/api/collections/inbox/check").json()
    assert res["processed"] == 1 and res["results"][0]["outcome"] == "needs_attention"
    na = client.get("/api/collections/needs-attention").json()
    assert len(na) == 1 and na[0]["customer_name"].startswith("Kestrel") and na[0]["inbound"]["body"].startswith("Can you give us more time")
    conv = client.get(f"/api/collections/conversations/{na[0]['conversation_id']}").json()
    assert [m["direction"] for m in conv["messages"]] == ["outbound", "inbound", "outbound"]
    bad = client.post(f"/api/collections/messages/{na[0]['message_id']}/send", json={"body": "INV-1004 for ₹1.00"})
    assert bad.status_code == 422 and bad.json()["problems"]
    ok = client.post(f"/api/collections/messages/{na[0]['message_id']}/send", json={"body": "Thank you, we will review and revert.", "subject": "Re: reminder"})
    assert ok.status_code == 200 and client.get("/api/collections/needs-attention").json() == []
