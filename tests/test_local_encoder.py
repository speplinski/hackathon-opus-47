"""Tests for ``src/auditable_design/embedders/local_encoder.py``.

Organisation
------------
- ``TestEmptyInput`` / ``TestSeedContract`` — fast, no model load. Cover
  the two explicit fail-loud contracts (empty input raises, ``seed`` is
  keyword-only required).
- ``TestEncodeRealModel`` / ``TestProvenanceDict`` — require
  ``sentence-transformers/all-MiniLM-L6-v2``. First run downloads
  ~80 MB of weights into the Hugging Face cache; subsequent runs are
  fully local.

No markers are used to separate slow from fast tests — the hackathon
scale keeps the full test pass under ~10 s once weights are cached,
which is still well under the test-suite-wide budget.
"""

from __future__ import annotations

import numpy as np
import pytest

from auditable_design.embedders.local_encoder import (
    DEFAULT_MODEL,
    encode,
    model_weights_hash,
)

# ---------------------------------------------------------------------------
# Fast contract tests — no model load
# ---------------------------------------------------------------------------


class TestEmptyInput:
    """Empty input is a caller bug, not a zero-cost no-op."""

    def test_empty_list_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="empty input"):
            encode([], seed=0)


class TestSeedContract:
    """``seed`` must be passed explicitly — no silent default."""

    def test_seed_is_required_keyword_only(self) -> None:
        # ``texts`` alone — no ``seed`` keyword provided.
        with pytest.raises(TypeError, match="seed"):
            encode(["hello"])  # type: ignore[call-arg]

    def test_seed_cannot_be_positional(self) -> None:
        # Prevent future refactors from quietly making ``seed`` positional
        # and accidentally letting callers swap the argument order.
        with pytest.raises(TypeError):
            encode(["hello"], 0)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Real-model tests — require model weights (~80 MB first-time download)
# ---------------------------------------------------------------------------


class TestEncodeRealModel:
    """End-to-end encoding against the default MiniLM checkpoint."""

    def test_shape_and_dtype(self) -> None:
        texts = ["hello world", "goodbye world"]
        embeddings, _provenance = encode(texts, seed=0)
        assert embeddings.shape == (2, 384)
        assert embeddings.dtype == np.float32

    def test_rows_are_unit_norm(self) -> None:
        # The encoder raises RuntimeError if this contract is broken,
        # so a successful call already proves it. Asserting explicitly
        # here documents the intent and pins the tolerance.
        texts = ["alpha", "beta", "gamma"]
        embeddings, _provenance = encode(texts, seed=0)
        norms = np.linalg.norm(embeddings, axis=1)
        assert np.allclose(norms, 1.0, atol=1e-5)

    def test_byte_identical_under_same_seed(self) -> None:
        # If this fails on a fresh environment, one of the runtime
        # tuple elements drifted (torch, sentence-transformers, numpy,
        # platform). The provenance dict should tell you which one.
        texts = ["determinism check"]
        embeddings_a, _ = encode(texts, seed=42)
        embeddings_b, _ = encode(texts, seed=42)
        assert np.array_equal(embeddings_a, embeddings_b)


class TestProvenanceDict:
    """Every key the L3 meta-sidecar is expected to record."""

    # Minimum contract, not exhaustive: the encoder may grow additional
    # provenance keys over time (e.g. tokenizer_version) without breaking
    # downstream readers that only look for this subset. Tests below use
    # ``<=`` (subset) rather than ``==`` (equality) to preserve that
    # additive-compatible property. If you want to force the encoder to
    # declare every new key at the test level, flip those assertions to
    # set-equality and update this frozenset on every addition.
    REQUIRED_KEYS: frozenset[str] = frozenset(
        {
            "model_name",
            "model_weights_hash",
            "embedding_dim",
            "normalize_embeddings",
            "seed",
            "device",
            "torch_version",
            "sentence_transformers_version",
            "numpy_version",
            "python_version",
            "platform",
        }
    )

    def test_all_required_keys_present(self) -> None:
        _embeddings, provenance = encode(["hello"], seed=0)
        assert self.REQUIRED_KEYS <= set(provenance.keys())

    def test_provenance_value_types(self) -> None:
        # Types only — see ``test_provenance_sentinel_values`` for the
        # specific values. Split out so a failing type assertion and a
        # failing sentinel-value assertion produce distinct, crisp
        # failure messages instead of one opaque flat block.
        _embeddings, provenance = encode(["hello"], seed=0)
        assert isinstance(provenance["model_name"], str)
        assert isinstance(provenance["model_weights_hash"], str)
        assert isinstance(provenance["embedding_dim"], int)
        assert isinstance(provenance["normalize_embeddings"], bool)
        assert isinstance(provenance["seed"], int)
        assert isinstance(provenance["device"], str)
        assert isinstance(provenance["torch_version"], str)
        assert isinstance(provenance["sentence_transformers_version"], str)
        assert isinstance(provenance["numpy_version"], str)
        assert isinstance(provenance["python_version"], str)
        assert isinstance(provenance["platform"], str)

    def test_provenance_sentinel_values(self) -> None:
        # The specific values that reviewers rely on when diffing
        # meta-sidecars between runs. A change to any of these is
        # load-bearing on ADR-011 replay semantics.
        _embeddings, provenance = encode(["hello"], seed=0)
        assert provenance["model_name"] == DEFAULT_MODEL
        assert len(provenance["model_weights_hash"]) == 16
        assert provenance["embedding_dim"] == 384
        assert provenance["normalize_embeddings"] is True
        assert provenance["seed"] == 0
        # Literal ``"cpu"`` on purpose — this is an audit-trail assertion
        # that the constructor pin (``device="cpu"``) was honoured, not
        # a reflection of ``model.device``. If someone ever removes the
        # pin and lets torch auto-select, the constant "cpu" here makes
        # the drift diagnosable instead of quietly recording "cuda:0".
        assert provenance["device"] == "cpu"

    def test_model_weights_hash_stable(self) -> None:
        # Called twice in the same process — reloading the same
        # checkpoint must give the same state_dict hash. If this fails,
        # either sentence-transformers is non-deterministic on load
        # (it isn't, currently) or the hashing helper is.
        first = model_weights_hash(DEFAULT_MODEL)
        second = model_weights_hash(DEFAULT_MODEL)
        assert first == second

    def test_model_weights_hash_matches_encode_provenance(self) -> None:
        # The standalone helper and the helper called from inside
        # ``encode`` must agree — otherwise a reviewer using one to
        # pre-check before replay would get a different answer than
        # the pipeline writes in its sidecar.
        _embeddings, provenance = encode(["hello"], seed=0)
        standalone = model_weights_hash(DEFAULT_MODEL)
        assert provenance["model_weights_hash"] == standalone
