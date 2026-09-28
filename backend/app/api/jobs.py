"""Job status API for async forecast generation."""

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import get_current_user
from app.models.user import User
from app.services.job_queue import get_job
from app.services.permissions import user_has_permission

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
async def job_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    owner = job.get("user_id")
    if owner and owner != current_user.id and not user_has_permission(current_user, "admin"):
        raise HTTPException(403, "Not authorized to view this job")
    return job
