"""Regression tests for the Chroma embedding-function migration fallback.

Collections persisted before the ONNX migration (see ONNX_BUNDLED_MODEL in
app/services/vector_store.py) still carry the old sentence-transformers
embedding-function name in their Chroma config. Chroma's name-based conflict
check rejects the mismatch on every open even though the two backends embed
numerically identically, which without a fallback fails every single
document upload against a pre-existing install.
"""

from unittest.mock import MagicMock

import pytest

from app.services import vector_store


@pytest.fixture(autouse=True)
def _reset_vector_store_caches():
    vector_store.reset_collection_cache()
    vector_store.reset_embedding_fn_cache()
    yield
    vector_store.reset_collection_cache()
    vector_store.reset_embedding_fn_cache()


def test_get_collection_falls_back_on_stale_embedding_function_name(monkeypatch):
    ready_collection = MagicMock(name="collection")
    ready_collection.count.return_value = 0
    calls: list[object] = []

    def fake_get_or_create_collection(*, name, embedding_function=None, metadata=None):
        calls.append(embedding_function)
        if embedding_function is not None:
            raise ValueError(
                "An embedding function already exists in the collection "
                "configuration, and a new one is provided. If this is "
                "intentional, please embed documents separately. Embedding "
                "function conflict: new: onnx_mini_lm_l6_v2 vs persisted: "
                "sentence_transformer"
            )
        return ready_collection

    fake_client = MagicMock()
    fake_client.get_or_create_collection.side_effect = fake_get_or_create_collection
    monkeypatch.setattr(vector_store, "get_chroma_client", lambda: fake_client)

    result = vector_store.get_collection("context_documents")

    assert result is ready_collection
    assert len(calls) == 2
    assert calls[0] is not None, "first attempt should pass the configured embedding function"
    assert calls[1] is None, "fallback should omit it so Chroma uses its default (ONNX) function"


def test_get_collection_reraises_unrelated_value_error(monkeypatch):
    fake_client = MagicMock()
    fake_client.get_or_create_collection.side_effect = ValueError("disk full")
    monkeypatch.setattr(vector_store, "get_chroma_client", lambda: fake_client)

    with pytest.raises(ValueError, match="disk full"):
        vector_store.get_collection("context_documents")
