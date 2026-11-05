"""FastAPI application factory."""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings
from .db import connect
from .deps import AppContext
from .repositories.store import Store
from .routes import collections, core, reconciliation
from .security import RateLimiter
from .services.agent_service import AgentService
from .services.email_service import EmailService
from .services.llm_service import LLMService
from .services.reconciliation_service import ReconciliationService
from .services.seed_service import ensure_seeded

log = logging.getLogger("app")


def build_context(settings: Settings, db=None, mail=None, llm=None) -> AppContext:
    store = Store(db if db is not None else connect(settings))
    mail = mail or EmailService(settings)
    llm = llm or LLMService(settings)
    ensure_seeded(store, settings)
    return AppContext(settings, store, mail, llm, AgentService(store, settings, mail, llm), ReconciliationService(store, settings))


def _background(ctx: AppContext, stop: threading.Event):
    s = ctx.settings
    next_reminder = 0.0
    import time

    while not stop.wait(5):
        t = time.time()
        if s.imap_poll_seconds and ctx.mail.imap_configured and int(t) % max(s.imap_poll_seconds, 5) < 5:
            try:
                ctx.agent.check_inbox()
            except Exception as exc:  # noqa: BLE001
                log.warning("inbox poll failed: %s", exc)
        if s.auto_reminder_hours and t >= next_reminder:
            next_reminder = t + s.auto_reminder_hours * 3600
            try:
                ctx.agent.send_reminders()
            except Exception as exc:  # noqa: BLE001
                log.warning("auto reminder failed: %s", exc)


def create_app(settings: Settings | None = None, ctx: AppContext | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        problems = settings.validate_for_serving()
        if problems:
            raise RuntimeError("Invalid configuration: " + "; ".join(problems))
        app.state.ctx = ctx or build_context(settings)
        stop = threading.Event()
        t = threading.Thread(target=_background, args=(app.state.ctx, stop), daemon=True)
        t.start()
        yield
        stop.set()

    app = FastAPI(title="AcmeCorp Collections & Reconciliation API", version="1.0.0", lifespan=lifespan)
    app.state.limiter = RateLimiter()
    if ctx is not None:  # tests: context available without lifespan
        app.state.ctx = ctx
    app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_methods=["GET", "POST", "PATCH"],
                       allow_headers=["Authorization", "Content-Type"], allow_credentials=False)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # never leak internals
        log.exception("unhandled error")
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    app.include_router(core.router)
    app.include_router(collections.router)
    app.include_router(reconciliation.router)
    return app


def app_factory() -> FastAPI:  # uvicorn --factory app.main:app_factory
    return create_app()
