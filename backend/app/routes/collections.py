from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..deps import AppContext, Auth, get_ctx
from ..services.agent_service import AgentError
from ..services.email_service import EmailError

router = APIRouter(prefix="/api/collections", dependencies=[Auth])


class ReminderIn(BaseModel):
    customer_id: str | None = Field(default=None, max_length=20)
    dry_run: bool = False
    force: bool = False


class SendDraftIn(BaseModel):
    subject: str | None = Field(default=None, max_length=300)
    body: str = Field(max_length=10000)
    force: bool = False


@router.post("/reminders/send")
def send_reminders(body: ReminderIn, ctx: AppContext = Depends(get_ctx)):
    if not body.dry_run and not ctx.mail.smtp_configured:
        raise HTTPException(503, "SMTP is not configured. Set SMTP_* in .env (or EMAIL_DRY_RUN=true to only log).")
    return {"results": ctx.agent.send_reminders(body.customer_id, body.dry_run, body.force)}


@router.get("/reminders")
def reminders(ctx: AppContext = Depends(get_ctx)):
    return ctx.store.reminders()


@router.post("/inbox/check")
def check_inbox(ctx: AppContext = Depends(get_ctx)):
    try:
        return ctx.agent.check_inbox()
    except EmailError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/conversations")
def conversations(ctx: AppContext = Depends(get_ctx)):
    customers = {c["customer_id"]: c["name"] for c in ctx.store.customers()}
    out = []
    for c in ctx.store.conversations():
        msgs = ctx.store.messages(c["conversation_id"])
        out.append({**c, "customer_name": customers.get(c["customer_id"], "Unknown sender"), "message_count": len(msgs),
                    "pending": sum(1 for m in msgs if m["status"] == "pending_review"), "preview": (msgs[-1]["body"][:120] if msgs else "")})
    return out


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: str, ctx: AppContext = Depends(get_ctx)):
    c = ctx.store.conversation(conversation_id)
    if not c:
        raise HTTPException(404, "Conversation not found")
    cust = ctx.store.customer(c["customer_id"]) if c.get("customer_id") else None
    return {**c, "customer_name": cust["name"] if cust else "Unknown sender", "messages": ctx.store.messages(conversation_id)}


@router.get("/needs-attention")
def needs_attention(ctx: AppContext = Depends(get_ctx)):
    out = []
    for d in ctx.store.pending_messages():
        inbound = ctx.store.message(d.get("reply_to_message")) if d.get("reply_to_message") else None
        cust = ctx.store.customer(d["customer_id"]) if d.get("customer_id") else None
        out.append({**d, "customer_name": cust["name"] if cust else "Unknown sender", "inbound": inbound})
    return out


@router.post("/messages/{message_id}/send")
def send_draft(message_id: str, body: SendDraftIn, ctx: AppContext = Depends(get_ctx)):
    try:
        return ctx.agent.send_draft(message_id, body.subject, body.body, body.force)
    except AgentError as exc:
        return JSONResponse({"detail": str(exc), "problems": exc.problems}, status_code=exc.status)


@router.post("/messages/{message_id}/dismiss")
def dismiss(message_id: str, ctx: AppContext = Depends(get_ctx)):
    try:
        ctx.agent.dismiss_draft(message_id)
    except AgentError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return {"status": "dismissed"}


@router.get("/claims")
def claims(ctx: AppContext = Depends(get_ctx)):
    return ctx.store.claims()
