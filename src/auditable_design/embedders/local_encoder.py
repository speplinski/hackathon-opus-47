"""Local sentence-transformer encoder for L3 clustering.

Exposes :func:`encode`, which embeds a list of texts into a ``(n, dim)``
``float32`` numpy array and, atomically in the same call, returns a
provenance ``dict`` that L3 must persist in its meta-sidecar for ADR-011
replay compliance.

Determinism
-----------
Given a fixed runtime tuple — ``torch``, ``sentence-transformers``,
``numpy``, ``platform`` — and the same ``model_name``, ``seed``, and
input ``texts``, :func:`encode` returns byte-identical output. Changes
to any element of that tuple can produce different embeddings. The
provenance dict captures the full tuple so drift across versions is
*detectable* (not prevented) by a downstream reviewer comparing
meta-sidecars.

Model weights fingerprint
-------------------------
:func:`model_weights_hash` hashes the *actual loaded weights*
(``state_dict``), not just the model name string. A silent upgrade of
``sentence-transformers/all-MiniLM-L6-v2`` on the Hugging Face Hub
changes the hash; a reviewer re-running the pipeline can spot the drift
immediately.

No API calls
------------
sentence-transformers caches model weights in its default Hugging Face
cache dir (``~/.cache/huggingface``). First call downloads the weights
once; every subsequent call is fully local and network-free.

Test mocking note
-----------------
Heavy imports (``torch``, ``sentence_transformers``) are deferred into
the function body so test collection and unrelated imports don't pay
the ~2 s torch startup cost. Tests that need to mock the model should
patch via ``monkeypatch.setitem(sys.modules, "sentence_transformers",
fake)`` *before* calling :func:`encode`.
"""

from __future__ import annotations

import hashlib
import logging
import platform
import sys
from typing import Any

import numpy as np
import numpy.typing as npt

__all__ = [
    "DEFAULT_MODEL",
    "encode",
    "model_weights_hash",
]

_log = logging.getLogger(__name__)

DEFAULT_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
"""Default encoder: 384-dim MPNet-distilled model, ~80 MB, CPU-fast."""

_NORMALIZE: bool = True
"""Whether rows are L2-normalised.

Pulled out as a constant so the value passed to
``SentenceTransformer.encode(..., normalize_embeddings=...)`` and the
value written into the provenance dict come from one source. Preventing
silent drift between "what we actually did" and "what we recorded doing"
is the whole point of the provenance sidecar.
"""


def encode(
    texts: list[str],
    *,
    model_name: str = DEFAULT_MODEL,
    seed: int,
) -> tuple[npt.NDArray[np.float32], dict[str, Any]]:
    """Embed ``texts`` and return ``(embeddings, provenance)``.

    ``embeddings`` is a ``(len(texts), dim)`` ``float32`` ndarray with
    L2-normalised rows (``normalize_embeddings=True`` on the underlying
    ``SentenceTransformer.encode`` call), so cosine similarity reduces
    to a dot product. A runtime assertion verifies the unit-norm
    contract before return.

    ``provenance`` is a dict suitable for merging into an L3 artifact's
    meta-sidecar. Keys:

    - ``model_name``: HF identifier passed in
    - ``model_weights_hash``: short hash of the actual ``state_dict``
    - ``embedding_dim``: output dimension (e.g. 384 for MiniLM)
    - ``normalize_embeddings``: ``True`` (constant, but recorded so
      future encoder variants can flip it without silent breakage)
    - ``seed``: RNG seed used
    - ``device``: ``"cpu"`` — pinned explicitly. A GPU path would use
      different float32 kernels and break the byte-identical replay
      contract; recording the device lets a reviewer diagnose drift.
    - ``torch_version``: e.g. ``"2.3.1"``
    - ``sentence_transformers_version``: e.g. ``"3.0.1"``
    - ``numpy_version``: e.g. ``"1.26.4"``
    - ``python_version``: e.g. ``"3.11.9"``
    - ``platform``: output of ``platform.platform()``

    Args:
        texts: Non-empty list of strings to embed.
        model_name: Hugging Face model identifier.
        seed: RNG seed — keyword-only, required. No default is provided
            on purpose: callers (L3) must thread ``RunContext.seed``
            through explicitly to avoid silently producing embeddings
            under an accidental seed.

    Raises:
        ValueError: if ``texts`` is empty. L3 is expected to filter
            empty input upstream; a call with zero inputs is a bug, not
            a zero-cost no-op, and we fail loudly.
    """
    if not texts:
        raise ValueError("encode() called with empty input")

    # Heavy imports deferred — see module docstring "Test mocking note".
    import random

    import sentence_transformers
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    _log.info("loading sentence-transformer: %s", model_name)
    # Pin device to CPU: GPU kernels for float32 matmul/softmax can
    # produce byte-different (though numerically equivalent) outputs,
    # which would defeat the byte-identical replay contract. Encoding
    # the whole MiniLM-scale corpus on CPU for this hackathon is fast
    # enough that there's no reason to allow the drift surface.
    model = sentence_transformers.SentenceTransformer(model_name, device="cpu")

    embeddings = model.encode(
        texts,
        convert_to_numpy=True,
        normalize_embeddings=_NORMALIZE,
        show_progress_bar=False,
    )
    embeddings = embeddings.astype(np.float32, copy=False)

    # Verify the unit-norm contract at runtime. If this fails, the
    # ``sentence-transformers`` upstream default changed — downstream
    # cosine-via-dot-product math would silently break without this
    # guard.
    norms = np.linalg.norm(embeddings, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-5):
        raise RuntimeError(
            f"encoder returned non-unit-norm rows "
            f"(min={norms.min():.6f}, max={norms.max():.6f}); "
            f"sentence-transformers contract broken"
        )

    provenance: dict[str, Any] = {
        "model_name": model_name,
        "model_weights_hash": _weights_hash_from_model(model),
        "embedding_dim": int(embeddings.shape[1]),
        "normalize_embeddings": _NORMALIZE,
        "seed": seed,
        "device": "cpu",
        "torch_version": torch.__version__,
        "sentence_transformers_version": sentence_transformers.__version__,
        "numpy_version": np.__version__,
        "python_version": (
            f"{sys.version_info.major}."
            f"{sys.version_info.minor}."
            f"{sys.version_info.micro}"
        ),
        "platform": platform.platform(),
    }

    _log.debug(
        "encoded %d texts to %r float32",
        len(texts),
        embeddings.shape,
    )
    return embeddings, provenance


def model_weights_hash(model_name: str = DEFAULT_MODEL) -> str:
    """Return a stable short hash of a model's loaded ``state_dict``.

    Loads the model (respecting the deferred-import pattern in
    :func:`encode`), walks its ``state_dict`` in sorted-key order, and
    hashes ``(key_bytes + tensor_bytes)`` tuples. The resulting hex
    digest is stable across machines for the same weights.

    Note: this intentionally hashes *weights only*, not the tokenizer.
    A tokenizer change without a weights change is extremely rare for
    the ``all-MiniLM-*`` family and would surface via different output
    embeddings on any non-trivial corpus anyway.

    Separate from :func:`encode` because a caller occasionally wants
    the hash without running an embedding pass (e.g. to pre-check that
    cached L3 output matches the currently-installed weights before
    attempting a replay).

    No ``seed`` parameter is offered: ``SentenceTransformer`` load is
    deterministic with respect to the on-disk weights file, so seeding
    RNGs here would be theatre — a silent ``seed=0`` default on what
    is really a pure function would misdocument the contract.
    """
    import sentence_transformers

    model = sentence_transformers.SentenceTransformer(model_name, device="cpu")
    return _weights_hash_from_model(model)


def _weights_hash_from_model(model: Any) -> str:
    """Compute the state_dict hash of an already-loaded model.

    Private helper; :func:`encode` uses it to avoid re-loading the
    model just to hash its weights.
    """
    state = model.state_dict()
    hasher = hashlib.sha256()
    for key in sorted(state.keys()):
        hasher.update(key.encode("utf-8"))
        hasher.update(state[key].detach().cpu().numpy().tobytes())
    return hasher.hexdigest()[:16]
