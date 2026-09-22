"""Edit lock API for concurrent forecast edits."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.models.user import User
from app.services.locks import acquire_lock, release_lock, LockConflictError, purge_expired_locks

router = APIRouter(prefix="/locks", tags=["locks"])


class LockRequest(BaseModel):
    version_id: str
    line_item_id: int
    period: str
    ttl_seconds: int = 120


@router.post("/acquire")
async def lock_acquire(
    body: LockRequest,
    current_user: User = Depends(get_current_user),
):
    purge_expired_locks()
    try:
        entry = acquire_lock(
            body.version_id,
            body.line_item_id,
            body.period,
            current_user.id,
            username=current_user.username,
            ttl_seconds=body.ttl_seconds,
        )
        return {
            "success": True,
            "expires_at": entry["expires_at"].isoformat(),
        }
    except LockConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/release")
async def lock_release(
    body: LockRequest,
    current_user: User = Depends(get_current_user),
):
    ok = release_lock(body.version_id, body.line_item_id, body.period, current_user.id)
    if not ok:
        raise HTTPException(status_code=403, detail="Lock held by another user")
    return {"success": True}
