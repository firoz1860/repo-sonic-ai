"""Real SMTP round-trip against a local test server, MIME parsing, token and rate-limit checks."""
import time

import pytest
from aiosmtpd.controller import Controller
from email import message_from_bytes

from app.logic.collections.parser import parse_raw_email, strip_quoted, extract_claim_hints
from app.security import RateLimiter, make_token, verify_token
from app.services.email_service import EmailError, EmailService
from conftest import make_settings, raw_email


class Sink:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope)
        return "250 OK"


@pytest.fixture()
def smtp_server():
    sink = Sink()
    ctl = Controller(sink, hostname="127.0.0.1", port=8026)
    ctl.start()
    yield sink
    ctl.stop()


def test_smtp_send_threading_headers_and_injection_safety(smtp_server):
    s = make_settings(smtp_host="127.0.0.1", smtp_port=8026, smtp_security="none", smtp_user="", smtp_from="AcmeCorp Accounts <accounts@acme-test.dev>")
    svc = EmailService(s)
    mid = svc.send("cust@cust.test", "Re: Hello\r\nBcc: evil@x.test", "Hi ₹59,000.00", in_reply_to="<abc@cust.test>", references=["<abc@cust.test>"])
    env = smtp_server.messages[0]
    msg = message_from_bytes(env.content)
    assert env.rcpt_tos == ["cust@cust.test"] and msg["Message-ID"] == mid
    assert msg["In-Reply-To"] == "<abc@cust.test>" and msg["Bcc"] is None and "evil" not in str(msg["Subject"]).split("Bcc")[0]
    assert "₹59,000.00" in msg.get_payload(decode=True).decode("utf-8")
    with pytest.raises(EmailError):
        svc.send("bad address", "s", "b")
    with pytest.raises(EmailError):
        svc.send("a@b.test\r\nBcc: x@y.test", "s", "b")


def test_smtp_failure_is_reported():
    svc = EmailService(make_settings(smtp_host="127.0.0.1", smtp_port=1, smtp_security="none", smtp_from="a@b.test"))
    with pytest.raises(EmailError):
        svc.send("c@d.test", "s", "b")


def test_parse_mime_and_quoted_reply_stripping():
    raw = raw_email("Arjun <A@Cust.test>", "Re: Payment reminder", "We have paid INV-1004, UTR N123456789012.\n\nOn Mon, 3 Nov 2026, Accounts wrote:\n> INV-1004 for ₹59,000.00 ...\n> please pay")
    p = parse_raw_email(raw)
    assert p["from"] == "a@cust.test" and p["subject"] == "Re: Payment reminder"
    body = strip_quoted(p["body"])
    assert body == "We have paid INV-1004, UTR N123456789012." and "59,000" not in body
    h = extract_claim_hints(body, {"INV-1004"})
    assert h["invoice_numbers"] == ["INV-1004"] and h["reference"] == "N123456789012"


def test_html_only_email_is_converted_to_text():
    from email.message import EmailMessage

    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = "a@b.test", "x", "<h@x>"
    m.set_content("<html><body><p>What is my <b>balance</b>?</p><script>alert(1)</script></body></html>", subtype="html")
    assert "balance" in parse_raw_email(bytes(m))["body"] and "alert" not in parse_raw_email(bytes(m))["body"]


def test_token_tamper_and_expiry():
    t = make_token("s" * 32, 5)
    assert verify_token("s" * 32, t) == "admin"
    assert verify_token("other" * 8, t) is None
    p, sig = t.split(".")
    assert verify_token("s" * 32, p + "." + sig[:-2] + "aa") is None
    assert verify_token("s" * 32, make_token("s" * 32, -1)) is None


def test_login_rate_limit(ctx, settings):
    from fastapi.testclient import TestClient
    from app.main import create_app

    c = TestClient(create_app(settings, ctx))
    codes = [c.post("/api/auth/login", json={"password": "wrong"}).status_code for _ in range(10)]
    assert codes[:8] == [401] * 8 and codes[8:] == [429, 429]
    assert RateLimiter(2, 1).allow("k")


def test_server_refuses_to_start_without_secrets():
    from app.config import Settings

    assert Settings(admin_password="", secret_key="").validate_for_serving()
    assert not Settings(admin_password="longenough", secret_key="x" * 20).validate_for_serving()
