"""Context engine API — document upload, URL ingestion, search, and management."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.schemas.forecast import PanelDataResponse
from app.services import context_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/context", tags=["context"])


class IngestURLRequest(BaseModel):
    url: str
    scope: str = "user"
    tags: list[str] = []


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    file_type: Optional[str] = None
    scope: Optional[str] = None


# ──────────────────────────────────────────────────
# Document Upload
# ──────────────────────────────────────────────────


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    scope: str = Form("user"),
    conversation_id: Optional[str] = Form(None),
    tags: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload a document to the context engine for RAG indexing.
    
    Supports: PDF, DOCX, PPTX, XLSX, CSV, TXT, HTML, MD
    """
    allowed_extensions = {
        ".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".xls",
        ".csv", ".txt", ".md", ".html", ".htm",
    }
    import os

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not supported. Allowed: {sorted(allowed_extensions)}",
        )

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    doc = await context_engine.upload_document(
        db=db,
        file=file,
        user_id=current_user.id,
        scope=scope,
        conversation_id=conversation_id,
        tags=tag_list,
    )

    return {
        "id": doc.id,
        "original_name": doc.original_name,
        "file_type": doc.file_type,
        "status": doc.status,
        "chunk_count": doc.chunk_count,
        "description": doc.description,
        "error_message": doc.error_message,
    }


# ──────────────────────────────────────────────────
# URL Ingestion
# ──────────────────────────────────────────────────


@router.post("/ingest-url")
async def ingest_url(
    request: IngestURLRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fetch and index content from a URL."""
    if not request.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid URL. Must start with http:// or https://")

    doc = await context_engine.ingest_url(
        db=db,
        url=request.url,
        user_id=current_user.id,
        scope=request.scope,
        tags=request.tags,
    )

    return {
        "id": doc.id,
        "original_name": doc.original_name,
        "source_url": doc.source_url,
        "status": doc.status,
        "chunk_count": doc.chunk_count,
        "description": doc.description,
        "error_message": doc.error_message,
    }


# ──────────────────────────────────────────────────
# Document Management
# ──────────────────────────────────────────────────


@router.get("/documents")
async def list_documents(
    scope: Optional[str] = Query(None),
    conversation_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List documents visible to the current user."""
    docs = context_engine.list_documents(
        db=db,
        user_id=current_user.id,
        scope=scope,
        conversation_id=conversation_id,
    )
    return {"documents": docs, "total": len(docs)}


@router.get("/documents/{document_id}")
async def get_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get document details."""
    doc = context_engine.get_document(db, document_id, current_user.id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a document and all its indexed chunks."""
    deleted = context_engine.delete_document(db, document_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"deleted": True, "id": document_id}


# ──────────────────────────────────────────────────
# Semantic Search
# ──────────────────────────────────────────────────


@router.post("/search")
async def search_documents(
    request: SearchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Semantic search across user-accessible documents."""
    filters = {}
    if request.file_type:
        filters["file_type"] = request.file_type
    if request.scope:
        filters["scope"] = request.scope

    results = context_engine.search_documents(
        db=db,
        query=request.query,
        user_id=current_user.id,
        top_k=request.top_k,
        filters=filters,
    )
    return {"results": results, "query": request.query, "total": len(results)}


# ──────────────────────────────────────────────────
# Panel Data Endpoint
# ──────────────────────────────────────────────────


@router.get("/document-panel/{conversation_id}", response_model=PanelDataResponse)
async def get_document_panel(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Panel data for the Document Library panel."""
    docs = context_engine.list_documents(
        db=db,
        user_id=current_user.id,
        conversation_id=conversation_id,
    )

    total_chunks = sum(d.get("chunk_count", 0) for d in docs)
    total_size = sum(d.get("file_size_bytes", 0) for d in docs)
    type_counts: dict[str, int] = {}
    for d in docs:
        ft = d.get("file_type", "other")
        type_counts[ft] = type_counts.get(ft, 0) + 1

    return PanelDataResponse(
        panel_type="document_library",
        title="Document Library",
        data={
            "documents": docs,
            "summary": {
                "total_documents": len(docs),
                "total_chunks": total_chunks,
                "total_size_bytes": total_size,
                "type_counts": type_counts,
            },
        },
    )
