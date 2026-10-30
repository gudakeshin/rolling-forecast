"""Safe file upload helpers — UUID paths, size limits, magic-byte sniff."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import settings

# extension → accepted magic prefixes (empty = skip sniff, e.g. csv/txt)
MAGIC_BY_EXT: dict[str, list[bytes]] = {
    ".csv": [],
    ".txt": [],
    ".xlsx": [b"PK"],  # zip/ooxml
    ".xls": [b"\xd0\xcf\x11\xe0"],  # OLE
    ".pdf": [b"%PDF"],
    ".docx": [b"PK"],
    ".pptx": [b"PK"],
    ".html": [],
    ".htm": [],
}

DEFAULT_ALLOWED = {".csv", ".xlsx", ".xls"}


async def save_upload(
    file: UploadFile,
    *,
    dest_dir: str | None = None,
    allowed_extensions: set[str] | None = None,
    max_bytes: int | None = None,
) -> dict:
    """Stream upload to a UUID-named file. Never uses client filename for the path.

    Returns dict with stored_path, original_name, size_bytes, extension.
    Raises HTTPException 400/413 on validation failure.
    """
    allowed = allowed_extensions or DEFAULT_ALLOWED
    limit = max_bytes if max_bytes is not None else settings.max_upload_bytes
    directory = dest_dir or settings.upload_dir

    original = file.filename or "upload"
    # Strip any path components from the client-supplied name (metadata only)
    original_name = Path(original).name
    ext = Path(original_name).suffix.lower()
    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not supported. Allowed: {sorted(allowed)}",
        )

    os.makedirs(directory, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    stored_path = os.path.join(directory, stored_name)

    # Chunked read with size cap + magic sniff on first chunk
    total = 0
    first_chunk: bytes | None = None
    try:
        with open(stored_path, "wb") as out:
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                if first_chunk is None:
                    first_chunk = chunk[:16]
                total += len(chunk)
                if total > limit:
                    out.close()
                    os.unlink(stored_path)
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload exceeds maximum size of {limit} bytes",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception:
        if os.path.exists(stored_path):
            os.unlink(stored_path)
        raise

    expected_magics = MAGIC_BY_EXT.get(ext)
    if expected_magics and first_chunk is not None:
        if not any(first_chunk.startswith(m) for m in expected_magics):
            os.unlink(stored_path)
            raise HTTPException(
                status_code=400,
                detail=f"File content does not match extension '{ext}'",
            )

    return {
        "stored_path": stored_path,
        "stored_name": stored_name,
        "original_name": original_name,
        "extension": ext,
        "size_bytes": total,
    }
