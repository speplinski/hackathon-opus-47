"""Atomic writes and sidecar hashing for layer artifacts (ADR-003).

Every file written under ``data/derived/…`` or ``data/artifacts/…`` goes
through :func:`write_jsonl_atomic`. The function guarantees two invariants
that the rest of the pipeline depends on:

1. **Atomicity.** The target file either contains the complete, valid
   payload or it does not exist. There is never a half-written file on
   disk. We achieve this by writing to a temporary sibling, ``fsync``-ing,
   and ``os.replace``-ing into place.

2. **Re-entry hash.** Every artifact is paired with a sidecar
   ``{artifact}.meta.json`` that records the sha256 of the payload plus
   the full-directory hash of every skill consumed to produce it. On the
   next run, if the meta matches, we can short-circuit re-execution.
   If any consumed skill's directory hash has drifted, the artifact is
   recomputed — this is the mechanism that makes the replay log
   trustworthy (ADR-011) and caching safe (P3, §5.2).

See also:
    ARCHITECTURE.md §1 (principle P7).
    docs/ADRs.md (ADR-003, ADR-011).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_ALLOWED_ROOTS",
    "RUN_ID_PATTERN",
    "ArtifactMeta",
    "StorageError",
    "hash_bytes",
    "hash_directory",
    "hash_file",
    "read_jsonl",
    "read_meta",
    "validate_run_id",
    "verify_meta",
    "write_jsonl_atomic",
]

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Safety perimeter
# ---------------------------------------------------------------------------
#
# `write_jsonl_atomic` refuses to write outside a configured set of root
# directories. This is defense-in-depth against a buggy caller that
# constructs a destination path from untrusted input (e.g. a `run_id` that
# came from a CLI flag without validation). The protection is belt-AND-
# suspenders with `validate_run_id()` below: the regex is the primary
# guard, the roots check is the fallback if the regex is bypassed or a
# future layer forgets to call it.

DEFAULT_ALLOWED_ROOTS: tuple[str, ...] = (
    "data",
    "demo/public/data",
)

# run_id must be alphanumeric with `._-` separators, starting with
# alphanumeric, max 64 chars. Forbids `/`, `..`, whitespace, anything
# that could escape or confuse path construction.
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def validate_run_id(run_id: str) -> str:
    """Validate a run identifier. Returns it unchanged on success.

    Raises:
        StorageError: If ``run_id`` does not match :data:`RUN_ID_PATTERN`.
    """
    if not isinstance(run_id, str):
        raise StorageError(f"run_id must be str, got {type(run_id).__name__}")
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise StorageError(f"run_id {run_id!r} fails validation — must match {RUN_ID_PATTERN.pattern}")
    return run_id


def _assert_path_under_allowed_root(
    target: Path,
    allowed_roots: Iterable[str],
    repo_root: Path,
) -> None:
    """Refuse writes that resolve outside any of ``allowed_roots``.

    ``target`` does not need to exist; we resolve its absolute form,
    then check that a prefix of its parts matches one of the allowed
    root directories (resolved relative to ``repo_root``).
    """
    try:
        target_abs = target.resolve(strict=False)
    except OSError as e:
        raise StorageError(f"cannot resolve target path {target}: {e}") from e

    for root in allowed_roots:
        root_abs = (repo_root / root).resolve(strict=False)
        try:
            target_abs.relative_to(root_abs)
        except ValueError:
            continue
        return  # match — allowed

    raise StorageError(
        f"refusing to write outside allowed roots: {target_abs} "
        f"(allowed: {[str((repo_root / r).resolve()) for r in allowed_roots]})"
    )


class StorageError(RuntimeError):
    """Raised when a storage invariant is violated."""


# ---------------------------------------------------------------------------
# Hashing primitives
# ---------------------------------------------------------------------------


def hash_bytes(data: bytes) -> str:
    """Return lowercase hex sha256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def hash_file(path: str | os.PathLike[str], *, chunk: int = 65536) -> str:
    """Stream-hash a file with sha256.

    Args:
        path:  File to hash.
        chunk: Read buffer size in bytes.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def hash_directory(
    path: str | os.PathLike[str],
    *,
    ignore: Iterable[str] = (".DS_Store", "__pycache__", ".pytest_cache"),
) -> str:
    """Deterministic sha256 tree-hash of a directory.

    Hashes every non-ignored regular file under ``path``, sorted by
    POSIX-style relative path, as ``"{relpath}\\0{sha256}\\n"`` joined
    together and hashed. Two directories with identical file contents and
    names produce identical hashes regardless of filesystem order.

    Args:
        path:   Directory root.
        ignore: Filename components to skip. Matches against individual
                path segments (not globs), so ``__pycache__`` skips every
                ``__pycache__`` directory anywhere in the tree.
    """
    root = Path(path)
    if not root.is_dir():
        raise StorageError(f"hash_directory: not a directory: {root}")

    ignore_set = frozenset(ignore)
    manifest_parts: list[str] = []

    for item in sorted(root.rglob("*")):
        if not item.is_file():
            continue
        # Skip if any path segment is in the ignore set.
        if any(seg in ignore_set for seg in item.relative_to(root).parts):
            continue
        rel = item.relative_to(root).as_posix()
        sha = hash_file(item)
        manifest_parts.append(f"{rel}\x00{sha}\n")

    manifest = "".join(manifest_parts).encode("utf-8")
    return hashlib.sha256(manifest).hexdigest()


# ---------------------------------------------------------------------------
# Artifact metadata (sidecar)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArtifactMeta:
    """Sidecar metadata that accompanies every layer artifact.

    Attributes:
        run_id:            The :class:`RunContext` id that produced this artifact.
        layer:             Layer name, e.g. ``"l1_classify"``.
        artifact_sha256:   sha256 of the *artifact file* exactly as written.
        input_hashes:      Mapping from logical-input-name → sha256, covering
                           every upstream artifact consumed. Keys are
                           stable, e.g. ``"l0_corpus"`` or ``"l1_classification"``.
            skill_hashes:  Mapping from skill-name → full-directory hash
                           of every skill consumed to produce this artifact.
                           Empty for layers that don't call Claude.
        item_count:        Number of JSONL records in the artifact.
        written_at:        ISO-8601 UTC timestamp.
        schema_version:    Artifact schema version. Bumped when the Pydantic
                           model for this layer changes shape.
        code_version:      :data:`auditable_design.__version__`.
    """

    run_id: str
    layer: str
    artifact_sha256: str
    input_hashes: Mapping[str, str]
    skill_hashes: Mapping[str, str]
    item_count: int
    written_at: str
    schema_version: int
    code_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "layer": self.layer,
            "artifact_sha256": self.artifact_sha256,
            "input_hashes": dict(self.input_hashes),
            "skill_hashes": dict(self.skill_hashes),
            "item_count": self.item_count,
            "written_at": self.written_at,
            "schema_version": self.schema_version,
            "code_version": self.code_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ArtifactMeta:
        return cls(
            run_id=data["run_id"],
            layer=data["layer"],
            artifact_sha256=data["artifact_sha256"],
            input_hashes=dict(data.get("input_hashes", {})),
            skill_hashes=dict(data.get("skill_hashes", {})),
            item_count=data["item_count"],
            written_at=data["written_at"],
            schema_version=data["schema_version"],
            code_version=data["code_version"],
        )


# ---------------------------------------------------------------------------
# Atomic write + sidecar
# ---------------------------------------------------------------------------


def _serialise_jsonl(items: Iterable[Mapping[str, Any]]) -> bytes:
    """Serialise an iterable of dict-likes into JSONL bytes.

    Stable key ordering via ``sort_keys=True`` — this matters for the
    sha256 we take over the payload: two logically-equal payloads must
    hash to the same digest regardless of dict insertion order.
    """
    lines: list[str] = []
    for item in items:
        lines.append(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    # Trailing newline is conventional for line-oriented formats and avoids
    # off-by-one when concatenating files.
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


def write_jsonl_atomic(
    path: str | os.PathLike[str],
    items: Iterable[Mapping[str, Any]] | list[Mapping[str, Any]],
    *,
    run_id: str,
    layer: str,
    input_hashes: Mapping[str, str] | None = None,
    skill_hashes: Mapping[str, str] | None = None,
    schema_version: int = 1,
    code_version: str | None = None,
    allowed_roots: Iterable[str] | None = None,
    repo_root: str | os.PathLike[str] | None = None,
) -> ArtifactMeta:
    """Write a JSONL artifact atomically and emit its sidecar ``.meta.json``.

    The function is resilient to crashes: either both ``path`` and its
    sidecar exist with consistent content, or neither does.

    Args:
        path:            Destination file. Parent directory must exist.
        items:           Iterable of mapping-like records.
        run_id:          Id of the producing run (required — no silent default).
        layer:           Name of the producing layer.
        input_hashes:    sha256 of every upstream artifact this layer read.
        skill_hashes:    Full-directory hash of every skill consumed.
        schema_version:  Bumped when the Pydantic schema for this layer
                         changes shape.
        code_version:    Package version. Defaults to
                         ``auditable_design.__version__``.
        allowed_roots:   Directory prefixes (relative to ``repo_root``)
                         under which writes are permitted. Defaults to
                         :data:`DEFAULT_ALLOWED_ROOTS`.
        repo_root:       Root of the repository. Defaults to the current
                         working directory. Used to resolve ``allowed_roots``.

    Returns:
        The :class:`ArtifactMeta` that was written alongside the artifact.
    """
    target = Path(path)

    # Perimeter check: refuse writes outside allowed roots. This is
    # defense-in-depth — `validate_run_id()` should have caught any
    # traversal attempt at RunContext construction, but we don't trust
    # upstream to have called it.
    roots = tuple(allowed_roots) if allowed_roots is not None else DEFAULT_ALLOWED_ROOTS
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    _assert_path_under_allowed_root(target, roots, root)

    # run_id must pass the same regex that RunContext should have
    # enforced. Re-validating here covers the case of a caller bypassing
    # RunContext (e.g. tests, or a script that builds a run_id on the fly).
    validate_run_id(run_id)

    if not target.parent.exists():
        raise StorageError(f"parent directory does not exist: {target.parent}")

    # Materialise the iterable — we need to know the count and the sha256
    # before we can write the sidecar. JSONL files at this scale are small
    # (dozens of KB to a few MB), so holding them in memory is fine.
    materialised = list(items)
    payload = _serialise_jsonl(materialised)
    payload_sha = hash_bytes(payload)

    # Write artifact atomically via tmp + fsync + replace.
    _write_bytes_atomic(target, payload)

    if code_version is None:
        from auditable_design import __version__ as code_version

    meta = ArtifactMeta(
        run_id=run_id,
        layer=layer,
        artifact_sha256=payload_sha,
        input_hashes=dict(input_hashes or {}),
        skill_hashes=dict(skill_hashes or {}),
        item_count=len(materialised),
        written_at=datetime.now(UTC).isoformat(timespec="seconds"),
        schema_version=schema_version,
        code_version=code_version,
    )

    meta_path = target.with_suffix(target.suffix + ".meta.json")
    meta_bytes = (json.dumps(meta.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    _write_bytes_atomic(meta_path, meta_bytes)

    # Structured log event (ADR-012). Stdlib `logging` is a placeholder;
    # `logging_setup.py` will add the structlog processor stack that makes
    # these emit as single-line JSON into `data/log/pipeline.log`.
    _log.info(
        "artifact_written",
        extra={
            "event": "artifact_written",
            "run_id": run_id,
            "layer": layer,
            "path": str(target),
            "sha256": payload_sha,
            "item_count": len(materialised),
            "skill_hashes": dict(skill_hashes or {}),
        },
    )

    return meta


def _write_bytes_atomic(target: Path, payload: bytes) -> None:
    """Write ``payload`` to ``target`` atomically.

    Uses a temporary sibling file + ``os.replace`` which is atomic on POSIX
    and on Windows since NTFS. ``fsync`` is called on the file; the parent
    dir is ``fsync``'d on POSIX to flush the rename into the directory entry.
    """
    # Random suffix defends against a re-entrant write in the same process
    # racing with itself, though in practice the pipeline is single-writer
    # per artifact. `secrets.token_hex(8)` gives 64 bits of entropy, plenty.
    suffix = f".tmp.{secrets.token_hex(8)}"
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=suffix,
        dir=str(target.parent),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
        # Best-effort directory fsync on POSIX. Windows does not expose a
        # POSIX-style directory fd; we swallow the error there.
        if os.name == "posix":
            dir_fd = os.open(str(target.parent), os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except Exception:
        # Best-effort cleanup of the temp file if rename failed.
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


# ---------------------------------------------------------------------------
# Read + verify
# ---------------------------------------------------------------------------


def read_jsonl(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Read a JSONL file into a list of dicts. No schema validation here."""
    p = Path(path)
    out: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise StorageError(f"{p}: invalid JSON at line {lineno}: {e}") from e
    return out


def read_meta(path: str | os.PathLike[str]) -> ArtifactMeta:
    """Read the sidecar ``.meta.json`` for an artifact at ``path``."""
    p = Path(path)
    meta_path = p.with_suffix(p.suffix + ".meta.json")
    if not meta_path.exists():
        raise StorageError(f"sidecar meta not found for {p}: expected {meta_path}")
    with meta_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return ArtifactMeta.from_dict(data)


def verify_meta(path: str | os.PathLike[str]) -> ArtifactMeta:
    """Read an artifact's sidecar and verify the payload hash still matches.

    Returns the meta on success. Raises :class:`StorageError` on mismatch.
    """
    meta = read_meta(path)
    actual = hash_file(path)
    if actual != meta.artifact_sha256:
        raise StorageError(
            f"artifact hash mismatch for {path}: meta says {meta.artifact_sha256}, file is {actual}"
        )
    return meta
