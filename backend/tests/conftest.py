from __future__ import annotations

import json
from email.message import EmailMessage
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import build_context, create_app
from app.services.email_service import EmailService
from app.services.llm_service import LLMService

DATA = Path(__file__).resolve().parents[2] / "data"


class FakeMail(EmailService):
    """Records outgoing mail instead of using SMTP; inbound mail is fed in by the tests."""

    def __init__(self, settings):
        super().__init__(settings)
        self.sent: list[dict] = []
        self.inbox: list[bytes] = []

    @property
    def smtp_configured(self):
        return True

    @property
    def imap_configured(self):
        return True

    def send(self, to, subject, body, in_reply_to=None, references=None):
        from app.services.email_service import clean_header, valid_address, EmailError

        to = clean_header(to)
        if not valid_address(to):
            raise EmailError("invalid recipient")
        mid = f"<out-{len(self.sent) + 1}@acme-test.dev>"
        self.sent.append({"to": to, "subject": clean_header(subject), "body": body, "in_reply_to": in_reply_to, "references": references, "message_id": mid})
        return mid

    def fetch_unseen(self, handler):
        n = 0
        for raw in list(self.inbox):
            if handler(raw):
                n += 1
        self.inbox.clear()
        return n


def make_settings(**kw) -> Settings:
    s = Settings(mongodb_uri="mongomock://", admin_password="test-password", secret_key="x" * 32, as_of_date="2026-11-05",
                 smtp_from="accounts@acme-test.dev", org_accounts_email="accounts@acme-test.dev", data_dir=DATA, llm_provider="none")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def raw_email(sender, subject, body, msg_id="<in-1@cust.test>", in_reply_to=None, headers=None, to="accounts@acme-test.dev") -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = sender, to, subject, msg_id
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    for k, v in (headers or {}).items():
        m[k] = v
    m.set_content(body)
    return bytes(m)


def fake_llm(reply_json: dict | None = None, fail: bool = False):
    """LLMService whose HTTP transport returns a canned JSON answer (or fails)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(500, json={"error": "boom"})
        payload = reply_json() if callable(reply_json) else reply_json
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})

    s = make_settings(llm_provider="openai", llm_api_key="k", llm_model="m")
    return LLMService(s, transport=httpx.MockTransport(handler))


@pytest.fixture()
def settings():
    return make_settings()


@pytest.fixture()
def ctx(settings):
    return build_context(settings, mail=FakeMail(settings))


@pytest.fixture()
def customers_with_inboxes(ctx):
    for c in ctx.store.customers():
        ctx.store.set_customer_email(c["customer_id"], f"cust{c['customer_id'][-1]}@cust.test")
    return ctx


@pytest.fixture()
def client(ctx, settings):
    app = create_app(settings, ctx)
    c = TestClient(app)
    tok = c.post("/api/auth/login", json={"password": "test-password"}).json()["token"]
    c.headers["Authorization"] = f"Bearer {tok}"
    c.ctx = ctx
    return c
