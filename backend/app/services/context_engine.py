"""Context Engine — orchestrates document lifecycle and semantic retrieval.

Provides a unified interface for:
- Uploading and processing documents
- Ingesting URLs
- Semantic search across user-accessible documents
- Auto-retrieving relevant context for the MasterAgent
- Document management (list, delete)
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.models.document import Document, DocumentChunk
from app.services import document_processor, vector_store

logger = logging.getLogger(__name__)


async def upload_document(
    db: Session,
    file: UploadFile,
    user_id: str,
    scope: str = "user",
    conversation_id: str | None = None,
    tags: list[str] | None = None,
) -> Document:
    """Save an uploaded file to disk and trigger the processing pipeline."""
    os.makedirs(settings.context_upload_dir, exist_ok=True)

    file_type = document_processor.detect_file_type(file.filename or "unknown.txt")
    doc_id = str(uuid.uuid4())
    ext = Path(file.filename or "file").suffix
    stored_filename = f"{doc_id}{ext}"
    file_path = os.path.join(settings.context_upload_dir, stored_filename)

    content = await file.read()
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    doc = Document(
        id=doc_id,
        user_id=user_id,
        conversation_id=conversation_id,
        filename=stored_filename,
        original_name=file.filename or "unknown",
        file_type=file_type,
        scope=scope,
        status="processing",
        file_size_bytes=len(content),
        tags=tags or [],
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    doc = await document_processor.process_document(db, doc, file_path)
    return doc


async def ingest_url(
    db: Session,
    url: str,
    user_id: str,
    scope: str = "user",
    conversation_id: str | None = None,
    tags: list[str] | None = None,
) -> Document:
    """Fetch a URL, extract content, and index it in the context engine."""
    doc_id = str(uuid.uuid4())

    doc = Document(
        id=doc_id,
        user_id=user_id,
        conversation_id=conversation_id,
        filename=f"{doc_id}.html",
        original_name=url[:200],
        file_type="url",
        source_url=url,
        scope=scope,
        status="processing",
        tags=tags or [],
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    doc = await document_processor.process_document(db, doc, "")
    return doc


def search_documents(
    db: Session,
    query: str,
    user_id: str,
    conversation_id: str | None = None,
    top_k: int | None = None,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Semantic search across documents accessible to the user.
    
    Returns ranked chunks with source attribution.
    """
    k = top_k or settings.context_top_k
    search_filters = filters or {}

    results = vector_store.search(
        query=query,
        user_id=user_id,
        top_k=k,
        filters=search_filters,
    )

    enriched = []
    for r in results:
        doc_id = r["metadata"].get("document_id", "")
        enriched.append({
            "chunk_id": r["id"],
            "content": r["content"],
            "score": round(r["score"], 4),
            "document_id": doc_id,
            "original_name": r["metadata"].get("original_name", ""),
            "file_type": r["metadata"].get("file_type", ""),
            "chunk_index": r["metadata"].get("chunk_index", 0),
            "metadata": r["metadata"],
        })
    return enriched


def get_context_for_query(
    db: Session,
    query: str,
    user_id: str,
    conversation_id: str | None = None,
    top_k: int = 3,
) -> str:
    """Auto-retrieve relevant context for the MasterAgent system prompt.
    
    Returns a formatted string of relevant chunks, or empty string if none found.
    """
    results = search_documents(db, query, user_id, conversation_id, top_k=top_k)
    if not results:
        return ""

    context_parts = []
    for r in results:
        if r["score"] < 0.3:
            continue
        source = r["original_name"] or r["document_id"][:8]
        context_parts.append(f"[Source: {source}]\n{r['content']}")

    if not context_parts:
        return ""

    return "\n\n---\n\n".join(context_parts)


def list_documents(
    db: Session,
    user_id: str,
    scope: str | None = None,
    conversation_id: str | None = None,
) -> list[dict[str, Any]]:
    """List documents visible to the user."""
    query = db.query(Document).filter(Document.user_id == user_id)

    if scope:
        query = query.filter(Document.scope == scope)
    if conversation_id:
        query = query.filter(
            (Document.conversation_id == conversation_id)
            | (Document.scope.in_(["user", "global"]))
        )

    docs = query.order_by(Document.created_at.desc()).all()

    return [
        {
            "id": d.id,
            "original_name": d.original_name,
            "file_type": d.file_type,
            "scope": d.scope,
            "status": d.status,
            "chunk_count": d.chunk_count,
            "total_chars": d.total_chars,
            "file_size_bytes": d.file_size_bytes,
            "description": d.description,
            "tags": d.tags or [],
            "source_url": d.source_url,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in docs
    ]


def get_document(db: Session, document_id: str, user_id: str) -> dict[str, Any] | None:
    """Get a single document detail."""
    doc = (
        db.query(Document)
        .filter(Document.id == document_id, Document.user_id == user_id)
        .first()
    )
    if not doc:
        return None
    return {
        "id": doc.id,
        "original_name": doc.original_name,
        "file_type": doc.file_type,
        "scope": doc.scope,
        "status": doc.status,
        "chunk_count": doc.chunk_count,
        "total_chars": doc.total_chars,
        "file_size_bytes": doc.file_size_bytes,
        "description": doc.description,
        "tags": doc.tags or [],
        "source_url": doc.source_url,
        "error_message": doc.error_message,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }


def delete_document(db: Session, document_id: str, user_id: str) -> bool:
    """Delete a document, its chunks, and its vector embeddings."""
    doc = (
        db.query(Document)
        .filter(Document.id == document_id, Document.user_id == user_id)
        .first()
    )
    if not doc:
        return False

    vector_store.delete_document(document_id)

    file_path = os.path.join(settings.context_upload_dir, doc.filename)
    if os.path.exists(file_path):
        os.remove(file_path)

    db.delete(doc)
    db.commit()
    logger.info(f"Deleted document {document_id}")
    return True
