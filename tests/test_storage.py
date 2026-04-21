"""Tests for atomic writes + sidecar hashing (ADR-003)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from auditable_design.storage import (
    DEFAULT_ALLOWED_ROOTS,
    RUN_ID_PATTERN,
    StorageError,
    hash_bytes,
    hash_directory,
    hash_file,
    read_jsonl,
    read_meta,
    validate_run_id,
    verify_meta,
    write_jsonl_atomic,
)

# ---------------------------------------------------------------------------
# Hashing primitives
# ---------------------------------------------------------------------------


def test_hash_bytes_is_sha256() -> None:
    # Known answer: sha256 of empty bytes.
    assert hash_bytes(b"") == ("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")


def test_hash_file_matches_hash_bytes(tmp_path: Path) -> None:
    p = tmp_path / "x.txt"
    p.write_bytes(b"hello world\n")
    assert hash_file(p) == hash_bytes(b"hello world\n")


def test_hash_directory_is_deterministic(tmp_path: Path) -> None:
    d = tmp_path / "skill"
    d.mkdir()
    (d / "SKILL.md").write_text("# Skill\n")
    (d / "prompt.txt").write_text("hello\n")

    # Add a nested file to ensure tree traversal covers sub-dirs.
    (d / "examples").mkdir()
    (d / "examples" / "ex1.yaml").write_text("k: v\n")

    a = hash_directory(d)
    b = hash_directory(d)
    assert a == b
    assert len(a) == 64


def test_hash_directory_ignores_pycache(tmp_path: Path) -> None:
    d = tmp_path / "skill"
    d.mkdir()
    (d / "SKILL.md").write_text("# Skill\n")
    h_clean = hash_directory(d)

    # Simulate a stray __pycache__ directory — it must not affect the hash.
    cache = d / "__pycache__"
    cache.mkdir()
    (cache / "foo.cpython-312.pyc").write_bytes(b"\x00\x01\x02")
    h_with_cache = hash_directory(d)

    assert h_clean == h_with_cache


def test_hash_directory_rejects_file(tmp_path: Path) -> None:
    f = tmp_path / "not_a_dir.txt"
    f.write_text("x")
    with pytest.raises(StorageError):
        hash_directory(f)


def test_hash_directory_detects_content_change(tmp_path: Path) -> None:
    d = tmp_path / "skill"
    d.mkdir()
    (d / "SKILL.md").write_text("version A\n")
    a = hash_directory(d)

    (d / "SKILL.md").write_text("version B\n")
    b = hash_directory(d)

    assert a != b


# ---------------------------------------------------------------------------
# Atomic write + sidecar
#
# All tests below go through `_write()` which whitelists the pytest
# `tmp_path` as an allowed root. Two tests below this block verify the
# perimeter check itself.
# ---------------------------------------------------------------------------


def _write(
    tmp_path: Path,
    out: Path,
    items: list[dict] | list,
    *,
    run_id: str = "test-run",
    layer: str = "l1",
    skill_hashes: dict | None = None,
):
    """Wrapper that authorises pytest's tmp_path as an allowed write root."""
    return write_jsonl_atomic(
        out,
        items,
        run_id=run_id,
        layer=layer,
        skill_hashes=skill_hashes,
        allowed_roots=[str(tmp_path)],
    )


def test_write_jsonl_atomic_roundtrips(tmp_path: Path) -> None:
    out = tmp_path / "l1.jsonl"
    items = [
        {"id": "r-1", "label": "ux", "confidence": 0.91},
        {"id": "r-2", "label": "content", "confidence": 0.33},
    ]
    meta = _write(tmp_path, out, items, run_id="2026-04-21_test", layer="l1_classify")

    assert out.exists()
    assert out.with_suffix(".jsonl.meta.json").exists()
    assert meta.item_count == 2
    assert meta.layer == "l1_classify"
    assert meta.run_id == "2026-04-21_test"

    # Reading the JSONL back returns the same records.
    recovered = read_jsonl(out)
    assert recovered == items

    # Reading the sidecar returns the same meta.
    assert read_meta(out).to_dict() == meta.to_dict()


def test_write_jsonl_atomic_hash_matches_file(tmp_path: Path) -> None:
    out = tmp_path / "l1.jsonl"
    meta = _write(tmp_path, out, [{"id": "r-1", "label": "ux"}])
    assert meta.artifact_sha256 == hash_file(out)


def test_verify_meta_succeeds_on_intact_artifact(tmp_path: Path) -> None:
    out = tmp_path / "l1.jsonl"
    _write(tmp_path, out, [{"id": "r-1"}])
    verify_meta(out)  # no exception ⇒ integrity verified


def test_verify_meta_fails_on_tampered_artifact(tmp_path: Path) -> None:
    out = tmp_path / "l1.jsonl"
    _write(tmp_path, out, [{"id": "r-1"}])

    # Tamper with the artifact without updating the sidecar.
    out.write_bytes(out.read_bytes() + b'{"id":"r-2"}\n')

    with pytest.raises(StorageError, match="hash mismatch"):
        verify_meta(out)


def test_write_jsonl_atomic_is_atomic_on_overwrite(tmp_path: Path) -> None:
    """An overwrite of an existing artifact should never leave the old file half-replaced."""
    out = tmp_path / "l1.jsonl"
    _write(tmp_path, out, [{"id": "v1"}], run_id="run-one")
    old_bytes = out.read_bytes()

    _write(tmp_path, out, [{"id": "v2"}], run_id="run-two")
    new_bytes = out.read_bytes()

    assert old_bytes != new_bytes
    # No stray tmp files left behind.
    leftovers = [p for p in tmp_path.iterdir() if ".tmp." in p.name]
    assert leftovers == [], f"leftover temp files: {leftovers}"


def test_write_jsonl_atomic_records_skill_hashes(tmp_path: Path) -> None:
    # Build a pretend skill directory and hash it.
    skill_dir = tmp_path / "skills" / "norman"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Norman audit skill\n")
    skill_hash = hash_directory(skill_dir)

    out = tmp_path / "l4.jsonl"
    meta = _write(
        tmp_path,
        out,
        [{"cluster_id": "c1", "skill": "norman", "severity": 7}],
        layer="l4_audit",
        skill_hashes={"norman": skill_hash},
    )
    assert meta.skill_hashes["norman"] == skill_hash

    # And reading the sidecar back preserves it.
    recovered = read_meta(out)
    assert recovered.skill_hashes["norman"] == skill_hash


def test_write_jsonl_atomic_missing_parent_dir_raises(tmp_path: Path) -> None:
    out = tmp_path / "does_not_exist" / "l1.jsonl"
    with pytest.raises(StorageError, match="parent directory does not exist"):
        _write(tmp_path, out, [{"x": 1}])


def test_write_jsonl_atomic_sort_keys_stable_hash(tmp_path: Path) -> None:
    """Two logically-equal payloads with different insertion order must hash equally."""
    a_out = tmp_path / "a.jsonl"
    b_out = tmp_path / "b.jsonl"

    meta_a = _write(tmp_path, a_out, [{"a": 1, "b": 2}])
    meta_b = _write(tmp_path, b_out, [{"b": 2, "a": 1}])
    assert meta_a.artifact_sha256 == meta_b.artifact_sha256


# ---------------------------------------------------------------------------
# Perimeter / input-validation guards (A-04)
# ---------------------------------------------------------------------------


def test_write_refuses_path_outside_allowed_roots(tmp_path: Path) -> None:
    """Writing outside `allowed_roots` must be refused — defense-in-depth against
    a caller that constructed the destination from an unvalidated string."""
    # `tmp_path / "allowed"` is the only allowed root. Try to write a
    # sibling path `tmp_path / "escaped.jsonl"` which is NOT under it.
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    escaped = tmp_path / "escaped.jsonl"

    with pytest.raises(StorageError, match="refusing to write outside allowed roots"):
        write_jsonl_atomic(
            escaped,
            [{"x": 1}],
            run_id="r",
            layer="l1",
            allowed_roots=[str(allowed)],
        )


def test_write_refuses_path_traversal_via_relative_parent(tmp_path: Path) -> None:
    """A path with `..` segments must resolve outside the allowed root and be refused."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    # Construct `allowed/../escaped.jsonl` — resolves to tmp_path/escaped.jsonl.
    traversal = allowed / ".." / "escaped.jsonl"

    with pytest.raises(StorageError, match="refusing to write"):
        write_jsonl_atomic(
            traversal,
            [{"x": 1}],
            run_id="r",
            layer="l1",
            allowed_roots=[str(allowed)],
        )


def test_validate_run_id_accepts_canonical() -> None:
    assert validate_run_id("2026-04-22_pilot") == "2026-04-22_pilot"
    assert validate_run_id("r") == "r"
    assert validate_run_id("a.b_c-d.1") == "a.b_c-d.1"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "../escape",
        "run with space",
        "run/with/slash",
        "\x00null",
        "run\nnewline",
        "a" * 65,  # too long
        "-starts-with-dash",
        ".starts-with-dot",
    ],
)
def test_validate_run_id_rejects_unsafe(bad: str) -> None:
    with pytest.raises(StorageError):
        validate_run_id(bad)


def test_write_rejects_invalid_run_id(tmp_path: Path) -> None:
    out = tmp_path / "l1.jsonl"
    with pytest.raises(StorageError, match="run_id"):
        write_jsonl_atomic(
            out,
            [{"x": 1}],
            run_id="../escape",
            layer="l1",
            allowed_roots=[str(tmp_path)],
        )


def test_default_allowed_roots_are_data_and_demo() -> None:
    """Sanity: the documented defaults haven't silently drifted."""
    assert DEFAULT_ALLOWED_ROOTS == ("data", "demo/public/data")


def test_run_id_pattern_is_anchored() -> None:
    """Pattern must be anchored — partial matches at either end would let
    `good_id; rm -rf /` slip through some callers' concatenation."""
    assert RUN_ID_PATTERN.pattern.startswith("^")
    assert RUN_ID_PATTERN.pattern.endswith("$")
    assert not RUN_ID_PATTERN.fullmatch("ok; rm -rf /")


def test_read_jsonl_rejects_malformed_line(tmp_path: Path) -> None:
    p = tmp_path / "bad.jsonl"
    p.write_text('{"ok": 1}\nthis-is-not-json\n')
    with pytest.raises(StorageError, match="invalid JSON"):
        read_jsonl(p)


def test_read_jsonl_skips_blank_lines(tmp_path: Path) -> None:
    p = tmp_path / "ok.jsonl"
    p.write_text('{"a":1}\n\n{"b":2}\n')
    assert read_jsonl(p) == [{"a": 1}, {"b": 2}]


def test_meta_sidecar_is_human_readable(tmp_path: Path) -> None:
    """The sidecar JSON is indented and sorted — diffable in PRs."""
    out = tmp_path / "l1.jsonl"
    _write(tmp_path, out, [{"x": 1}], run_id="r", layer="l1")
    meta_text = out.with_suffix(".jsonl.meta.json").read_text()
    # Not a single-line blob.
    assert "\n" in meta_text
    # Keys are sorted — 'artifact_sha256' appears before 'item_count'.
    data = json.loads(meta_text)
    keys = list(data.keys())
    assert keys == sorted(keys)
