from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..deps import AppContext, Auth, get_ctx
from ..services.reconciliation_service import ReconError

router = APIRouter(prefix="/api/reconciliation", dependencies=[Auth])


class AllocationIn(BaseModel):
    invoice_number: str = Field(max_length=20)
    amount: float | None = Field(default=None, gt=0)
    action: str | None = Field(default=None, pattern="^(close_tds|leave_open)$")


class ApproveIn(BaseModel):
    allocations: list[AllocationIn] | None = Field(default=None, max_length=12)


def _wrap(fn, *a):
    try:
        return fn(*a)
    except ReconError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.post("/statements")
async def upload(file: UploadFile = File(...), ctx: AppContext = Depends(get_ctx)):
    content = await file.read(ctx.settings.max_upload_bytes + 1)
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(415, "Upload a .csv file")
    return _wrap(ctx.recon.upload, file.filename, content)


@router.get("/statements")
def statements(ctx: AppContext = Depends(get_ctx)):
    return ctx.store.statements()


@router.get("/statements/{statement_id}")
def statement(statement_id: str, ctx: AppContext = Depends(get_ctx)):
    return _wrap(ctx.recon.statement_view, statement_id)


@router.post("/statements/{statement_id}/rematch")
def rematch(statement_id: str, ctx: AppContext = Depends(get_ctx)):
    _wrap(ctx.recon.statement_view, statement_id)
    ctx.recon.rematch(statement_id)
    return ctx.recon.statement_view(statement_id)


@router.post("/transactions/{txn_id}/approve")
def approve(txn_id: str, body: ApproveIn, ctx: AppContext = Depends(get_ctx)):
    allocs = [a.model_dump() for a in body.allocations] if body.allocations else None
    return _wrap(ctx.recon.approve, txn_id, allocs)


@router.post("/transactions/{txn_id}/reject")
def reject(txn_id: str, ctx: AppContext = Depends(get_ctx)):
    return _wrap(ctx.recon.reject, txn_id)


@router.post("/transactions/{txn_id}/reopen")
def reopen(txn_id: str, ctx: AppContext = Depends(get_ctx)):
    return _wrap(ctx.recon.reopen, txn_id)


@router.get("/payments")
def payments(ctx: AppContext = Depends(get_ctx)):
    return ctx.store.payments()
