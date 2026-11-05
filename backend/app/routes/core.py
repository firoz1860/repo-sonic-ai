"""Auth, organization, customers, invoices, summary and reset."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..deps import AppContext, Auth, get_ctx
from ..logic.invoices import decorate, is_overdue
from ..logic.money import D, to_float
from ..security import check_password, make_token
from ..services.email_service import valid_address
from ..services.seed_service import seed

router = APIRouter(prefix="/api")


class LoginIn(BaseModel):
    password: str = Field(max_length=200)


class EmailIn(BaseModel):
    contact_email: str = Field(max_length=254)


class ResetIn(BaseModel):
    keep_emails: bool = False


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/auth/login")
def login(body: LoginIn, request: Request, ctx: AppContext = Depends(get_ctx)):
    ip = request.client.host if request.client else "unknown"
    if not request.app.state.limiter.allow(ip):
        raise HTTPException(429, "Too many login attempts. Try again in a few minutes.")
    if not check_password(ctx.settings.admin_password, body.password):
        raise HTTPException(401, "Wrong password")
    return {"token": make_token(ctx.settings.secret_key, ctx.settings.token_ttl_minutes)}


@router.get("/status", dependencies=[Auth])
def status(ctx: AppContext = Depends(get_ctx)):
    return {"today": ctx.settings.today().isoformat(), "smtp_configured": ctx.mail.smtp_configured, "imap_configured": ctx.mail.imap_configured,
            "llm": ctx.settings.llm_provider if ctx.llm.enabled else "none (rule-based offline mode)", "email_dry_run": ctx.settings.email_dry_run}


@router.get("/organization", dependencies=[Auth])
def organization(ctx: AppContext = Depends(get_ctx)):
    return ctx.store.organization()


def _customer_view(ctx: AppContext, c: dict, invoices: list[dict]) -> dict:
    today = ctx.settings.today()
    mine = [i for i in invoices if i["customer_id"] == c["customer_id"]]
    out = {k: v for k, v in c.items() if k != "contact_email_lc"}
    unpaid: dict[str, Decimal] = defaultdict(Decimal)
    overdue: dict[str, Decimal] = defaultdict(Decimal)
    for i in mine:
        if i["status"] == "sent":
            unpaid[i["currency"]] += D(i["balance_due"])
            if is_overdue(i, today):
                overdue[i["currency"]] += D(i["balance_due"])
    out.update(invoice_count=len(mine), overdue_count=sum(1 for i in mine if is_overdue(i, today)),
               unpaid={k: to_float(v) for k, v in unpaid.items()}, overdue={k: to_float(v) for k, v in overdue.items()})
    return out


@router.get("/customers", dependencies=[Auth])
def customers(ctx: AppContext = Depends(get_ctx)):
    inv = ctx.store.invoices()
    return [_customer_view(ctx, c, inv) for c in ctx.store.customers()]


@router.get("/customers/{customer_id}", dependencies=[Auth])
def customer(customer_id: str, ctx: AppContext = Depends(get_ctx)):
    c = ctx.store.customer(customer_id)
    if not c:
        raise HTTPException(404, "Customer not found")
    return {**_customer_view(ctx, c, ctx.store.invoices(customer_id=customer_id)),
            "invoices": [decorate(i, ctx.settings.today()) for i in ctx.store.invoices(customer_id=customer_id)]}


@router.patch("/customers/{customer_id}", dependencies=[Auth])
def update_customer(customer_id: str, body: EmailIn, ctx: AppContext = Depends(get_ctx)):
    email = body.contact_email.strip()
    if not valid_address(email):
        raise HTTPException(422, "Enter a valid email address")
    if ctx.store.customer(customer_id) is None:
        raise HTTPException(404, "Customer not found")
    other = ctx.store.customer_by_email(email)
    if other and other["customer_id"] != customer_id:
        raise HTTPException(409, f"{email} already belongs to {other['name']}. Each customer needs its own address so replies reach the right account.")
    if email.lower() == ctx.mail.own_address:
        raise HTTPException(422, "A customer cannot use the organization's own mailbox")
    ctx.store.set_customer_email(customer_id, email)
    return _customer_view(ctx, ctx.store.customer(customer_id), ctx.store.invoices())


@router.get("/invoices", dependencies=[Auth])
def invoices(customer_id: str | None = None, status: str | None = None, overdue: bool | None = None, ctx: AppContext = Depends(get_ctx)):
    flt = {}
    if customer_id:
        flt["customer_id"] = customer_id
    if status in ("draft", "sent", "paid"):
        flt["status"] = status
    today = ctx.settings.today()
    rows = [decorate(i, today) for i in ctx.store.invoices(**flt)]
    if overdue is not None:
        rows = [r for r in rows if r["is_overdue"] == overdue]
    return rows


@router.get("/invoices/{number}", dependencies=[Auth])
def invoice(number: str, ctx: AppContext = Depends(get_ctx)):
    inv = ctx.store.invoice(number)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    return {**decorate(inv, ctx.settings.today()), "payments": [p for p in ctx.store.payments() if p["invoice_number"] == number],
            "claims": [c for c in ctx.store.claims() if number in c.get("invoice_numbers", [])]}


@router.get("/summary", dependencies=[Auth])
def summary(ctx: AppContext = Depends(get_ctx)):
    today = ctx.settings.today()
    inv = ctx.store.invoices()
    unpaid, overdue = defaultdict(Decimal), defaultdict(Decimal)
    for i in inv:
        if i["status"] == "sent":
            unpaid[i["currency"]] += D(i["balance_due"])
            if is_overdue(i, today):
                overdue[i["currency"]] += D(i["balance_due"])
    return {"today": today.isoformat(), "invoice_counts": {s: sum(1 for i in inv if i["status"] == s) for s in ("draft", "sent", "paid")},
            "overdue_count": sum(1 for i in inv if is_overdue(i, today)), "unpaid": {k: to_float(v) for k, v in unpaid.items()},
            "overdue": {k: to_float(v) for k, v in overdue.items()}, "needs_attention": len(ctx.store.pending_messages())}


@router.post("/admin/reset", dependencies=[Auth])
def reset(body: ResetIn, ctx: AppContext = Depends(get_ctx)):
    return {"status": "reset", **seed(ctx.store, ctx.settings, keep_emails=body.keep_emails)}
