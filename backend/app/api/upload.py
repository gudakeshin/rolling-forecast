"""File upload endpoint for actuals data."""

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.upload_safety import save_upload

router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("/actuals")
async def upload_actuals(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload a CSV/Excel file containing actuals data.

    Client filename is metadata only — storage uses a UUID path.
    """
    saved = await save_upload(file, allowed_extensions={".csv", ".xlsx", ".xls"})
    return {
        "filename": saved["original_name"],
        "file_path": saved["stored_path"],
        "size_bytes": saved["size_bytes"],
        "message": (
            f"File '{saved['original_name']}' uploaded successfully. "
            "Use the chat to ingest it into the forecast system."
        ),
    }
