"""Embedding backend: ONNX by default, no torch, guardrails on misconfiguration.

The switch from sentence-transformers to Chroma's bundled ONNX graph removes
torch (and its CUDA wheels) from the runtime image. Both run the same
all-MiniLM-L6-v2 weights, so an existing Chroma index stays valid — these tests
pin that equivalence so a future backend change cannot silently invalidate it.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import Settings
from app.services import vector_store

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK = REPO_ROOT / "backend" / "requirements.lock"

EXPECTED_DIM = 384


@pytest.fixture(autouse=True)
def _restore_settings():
    original = vector_store.settings
    yield
    vector_store.settings = original
    vector_store.reset_embedding_fn_cache()


class TestDefaultBackend:
    def test_default_is_onnx(self):
        assert Settings().embedding_backend == "onnx"

    def test_builds_the_onnx_function(self):
        vector_store.reset_embedding_fn_cache()
        assert type(vector_store._get_embedding_fn()).__name__ == "ONNXMiniLM_L6_V2"

    def test_embeddings_have_expected_dimensionality(self):
        """A dimensionality change would silently corrupt an existing index."""
        vector_store.reset_embedding_fn_cache()
        vectors = vector_store._get_embedding_fn()(["rolling forecast variance"])
        assert len(vectors) == 1
        assert len(vectors[0]) == EXPECTED_DIM

    def test_embedding_path_does_not_import_torch(self):
        """Run in a subprocess: sys.modules is global, and another test in the
        same session may legitimately have imported torch already."""
        code = (
            "import sys;"
            "from app.services import vector_store;"
            "vector_store._get_embedding_fn()(['smoke']);"
            "sys.exit(1 if 'torch' in sys.modules else 0)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=REPO_ROOT / "backend",
            env={**os.environ, "APP_ENV": "test"},
            capture_output=True,
        )
        assert proc.returncode == 0, (
            "the default embedding path pulled in torch — the multi-GB "
            f"dependency this backend exists to avoid.\n{proc.stderr.decode()[-500:]}"
        )


class TestGuardrails:
    def test_onnx_rejects_a_different_model(self):
        """Chroma's ONNX function bundles one model; honouring EMBEDDING_MODEL
        silently would embed with weights the operator did not ask for."""
        vector_store.settings = Settings(
            embedding_backend="onnx", embedding_model="intfloat/e5-large"
        )
        vector_store.reset_embedding_fn_cache()
        with pytest.raises(ValueError, match="only provides"):
            vector_store._get_embedding_fn()

    def test_unknown_backend_is_rejected(self):
        vector_store.settings = Settings(embedding_backend="bogus")
        vector_store.reset_embedding_fn_cache()
        with pytest.raises(ValueError, match="Unsupported EMBEDDING_BACKEND"):
            vector_store._get_embedding_fn()

    def test_backend_name_is_case_and_space_insensitive(self):
        vector_store.settings = Settings(embedding_backend="  ONNX  ")
        vector_store.reset_embedding_fn_cache()
        assert type(vector_store._get_embedding_fn()).__name__ == "ONNXMiniLM_L6_V2"


class TestRuntimeDependencies:
    def _locked(self) -> set[str]:
        pins = re.findall(r"^([A-Za-z0-9._-]+)==", LOCK.read_text(), re.MULTILINE)
        return {re.sub(r"[-_.]+", "-", p).lower() for p in pins}

    @pytest.mark.parametrize("package", ["torch", "sentence-transformers", "triton"])
    def test_heavy_ml_stack_is_not_in_the_runtime_lock(self, package):
        assert package not in self._locked(), (
            f"{package} is back in requirements.lock; the ONNX embedding "
            "backend exists specifically to keep it out of the image"
        )

    def test_no_nvidia_cuda_wheels(self):
        cuda = sorted(p for p in self._locked() if p.startswith("nvidia-"))
        assert not cuda, f"CUDA wheels pulled into a CPU-only image: {cuda}"

    def test_onnxruntime_is_present(self):
        """The ONNX backend is useless without its runtime."""
        assert "onnxruntime" in self._locked()
