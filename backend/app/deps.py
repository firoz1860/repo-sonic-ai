from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request

from .config import Settings
from .repositories.store import Store
from .security import verify_token
from .services.agent_service import AgentService
from .services.email_service import EmailService
from .services.llm_service import LLMService
from .services.reconciliation_service import ReconciliationService


@dataclass
class AppContext:
    settings: Settings
    store: Store
    mail: EmailService
    llm: LLMService
    agent: AgentService
    recon: ReconciliationService


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def require_auth(request: Request, authorization: str | None = Header(default=None)) -> str:
    ctx: AppContext = request.app.state.ctx
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Authentication required")
    user = verify_token(ctx.settings.secret_key, authorization[7:].strip())
    if not user:
        raise HTTPException(401, "Invalid or expired token")
    return user


Auth = Depends(require_auth)
