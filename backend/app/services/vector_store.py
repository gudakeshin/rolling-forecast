"""ChromaDB vector store for context engine semantic search."""

import json
import logging
import os
from typing import Any

import chromadb
from chromadb.api.types import Metadata
from chromadb.utils import embedding_functions

from app.config import settings

logger = logging.getLogger(__name__)

_client: chromadb.ClientAPI | None = None
_collections: dict[str, chromadb.Collection] = {}
_embedding_fn = None

COLLECTION_NAME = "context_documents"
ARCHIVAL_COLLECTION_NAME = "archival_memory"


# all-MiniLM-L6-v2 ships with Chroma as an ONNX graph. It is the same model
# with the same weights as the sentence-transformers build (verified: max
# elementwise delta ~3e-7, identical neighbour ordering), so switching backends
# does NOT invalidate an existing index — but it drops torch, and with it
# several GB of CUDA wheels, from the runtime image.
ONNX_BUNDLED_MODEL = "all-MiniLM-L6-v2"


def _get_embedding_fn():
    global _embedding_fn
    if _embedding_fn is None:
        _embedding_fn = _build_embedding_fn()
    return _embedding_fn


def _build_embedding_fn():
    backend = (settings.embedding_backend or "onnx").strip().lower()

    if backend == "sentence_transformers":
        # Opt-in only: requires the sentence-transformers extra to be installed.
        try:
            return embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=settings.embedding_model
            )
        except Exception as exc:  # pragma: no cover — depends on optional extra
            raise RuntimeError(
                "EMBEDDING_BACKEND=sentence_transformers requires the "
                "'sentence-transformers' package, which is not installed by "
                "default (it pulls in torch). Install it explicitly or use "
                "EMBEDDING_BACKEND=onnx."
            ) from exc

    if backend != "onnx":
        raise ValueError(
            f"Unsupported EMBEDDING_BACKEND {backend!r}; expected 'onnx' or "
            "'sentence_transformers'"
        )

    if settings.embedding_model != ONNX_BUNDLED_MODEL:
        # Chroma's ONNX function bundles exactly one model; honouring
        # EMBEDDING_MODEL silently would embed with the wrong weights.
        raise ValueError(
            f"EMBEDDING_BACKEND=onnx only provides {ONNX_BUNDLED_MODEL!r}, but "
            f"EMBEDDING_MODEL is {settings.embedding_model!r}. Either set "
            f"EMBEDDING_MODEL={ONNX_BUNDLED_MODEL} or switch to "
            "EMBEDDING_BACKEND=sentence_transformers."
        )

    return embedding_functions.ONNXMiniLM_L6_V2()


def reset_embedding_fn_cache() -> None:
    """Drop the cached embedding function (used by tests and config reloads)."""
    global _embedding_fn
    _embedding_fn = None


def get_chroma_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        os.makedirs(settings.chroma_persist_dir, exist_ok=True)
        _client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        logger.info(f"ChromaDB initialized at {settings.chroma_persist_dir}")
    return _client


def get_collection(name: str = COLLECTION_NAME) -> chromadb.Collection:
    """Get (or create) a named Chroma collection, cached per process."""
    collection = _collections.get(name)
    if collection is None:
        client = get_chroma_client()
        collection = client.get_or_create_collection(
            name=name,
            embedding_function=_get_embedding_fn(),
            metadata={"hnsw:space": "cosine"},
        )
        _collections[name] = collection
        logger.info(f"ChromaDB collection '{name}' ready ({collection.count()} docs)")
    return collection


def reset_collection_cache() -> None:
    """Drop cached collection handles (used after client/persist-dir changes)."""
    _collections.clear()


def add_chunks(
    document_id: str,
    chunk_ids: list[str],
    texts: list[str],
    metadatas: list[dict[str, Any]],
) -> None:
    """Add document chunks to the vector store."""
    collection = get_collection()
    # Chroma only accepts scalar metadata values, so flatten first. The result
    # is a narrower type than the caller's dict[str, Any].
    safe_metadatas: list[Metadata] = []
    for meta in metadatas:
        flat: dict[str, str | int | float | bool | None] = {}
        for k, v in meta.items():
            if v is None:
                flat[k] = ""
            elif isinstance(v, (list, dict)):
                flat[k] = json.dumps(v)
            elif isinstance(v, (str, int, float, bool)):
                flat[k] = v
            else:
                flat[k] = str(v)
        safe_metadatas.append(flat)
    collection.add(
        ids=chunk_ids,
        documents=texts,
        metadatas=safe_metadatas,
    )
    logger.info(f"Added {len(chunk_ids)} chunks for document {document_id}")


def search(
    query: str,
    user_id: str,
    top_k: int = 5,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Semantic search over stored document chunks.
    
    Returns list of dicts with keys: id, content, score, metadata
    """
    collection = get_collection()
    where_filter: dict[str, Any] = {"user_id": user_id}
    if filters:
        if len(filters) > 0:
            conditions = [{"user_id": user_id}]
            for k, v in filters.items():
                if v is not None:
                    conditions.append({k: v})
            if len(conditions) > 1:
                where_filter = {"$and": conditions}

    try:
        results = collection.query(
            query_texts=[query],
            n_results=min(top_k, collection.count()) if collection.count() > 0 else top_k,
            where=where_filter if collection.count() > 0 else None,
        )
    except Exception as e:
        logger.warning(f"ChromaDB search failed: {e}")
        return []

    if not results or not results["ids"] or not results["ids"][0]:
        return []

    output = []
    for i, chunk_id in enumerate(results["ids"][0]):
        output.append({
            "id": chunk_id,
            "content": results["documents"][0][i] if results["documents"] else "",
            "score": 1 - (results["distances"][0][i] if results["distances"] else 0),
            "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
        })
    return output


def delete_document(document_id: str) -> int:
    """Remove all chunks for a document from the vector store."""
    collection = get_collection()
    try:
        existing = collection.get(where={"document_id": document_id})
        if existing and existing["ids"]:
            collection.delete(ids=existing["ids"])
            logger.info(f"Deleted {len(existing['ids'])} chunks for document {document_id}")
            return len(existing["ids"])
    except Exception as e:
        logger.warning(f"Error deleting document {document_id} from ChromaDB: {e}")
    return 0


def embed_query(query: str) -> list[float]:
    """Get embedding for a query string (for external use)."""
    fn = _get_embedding_fn()
    return fn([query])[0]
