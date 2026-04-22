"""Tests for `scripts/collect_reviews.py` — no network, no `.env` writes outside tmp.

Every test uses an injected fetcher and an isolated env file in `tmp_path`, so
re-running the suite never touches real Google Play and never mutates the
project's `.env`. The structure mirrors the script's sections (filters,
sampling, hashing, IO, orchestration).
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

# The script lives in `scripts/`, not a package. Load it by path so tests
# can import its symbols without pushing a package boundary into scripts/.
_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "collect_reviews.py"
_spec = importlib.util.spec_from_file_location("_collect_reviews_under_test", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
collect_reviews = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = collect_reviews
_spec.loader.exec_module(collect_reviews)

# Handy local aliases.
RawPayload = collect_reviews.RawPayload
_passes_filters = collect_reviews._passes_filters
stratified_sample = collect_reviews.stratified_sample
to_review = collect_reviews.to_review
author_hash = collect_reviews.author_hash
load_or_create_salt = collect_reviews.load_or_create_salt
collect = collect_reviews.collect
_write_corpus_atomic = collect_reviews._write_corpus_atomic
_write_manifest = collect_reviews._write_manifest
_read_existing_ids = collect_reviews._read_existing_ids
_read_existing_records = collect_reviews._read_existing_records
DEFAULT_DATE_FROM = collect_reviews.DEFAULT_DATE_FROM
DEFAULT_DATE_TO = collect_reviews.DEFAULT_DATE_TO


# --- Fixture helpers ---------------------------------------------------------


def _payload(
    *,
    user: str = "alice",
    content: str = "A" * 100,
    score: int = 2,
    at: datetime | None = None,
    app_version: str | None = "5.0.1",
    ext_id: str = "gp_1",
) -> RawPayload:
    """Build a RawPayload with defaults that pass every filter by default.

    Tests tweak one field at a time to assert precisely which rule bit.
    """
    return RawPayload(
        review_id_external=ext_id,
        user_name=user,
        content=content,
        score=score,
        at=at or datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
        app_version=app_version,
    )


# =============================================================================
# Filters
# =============================================================================


class TestFilters:
    def test_payload_in_window_passes(self) -> None:
        assert _passes_filters(_payload(), date_from=DEFAULT_DATE_FROM, date_to=DEFAULT_DATE_TO)

    def test_date_before_window_rejected(self) -> None:
        """Off-by-one guard against `<` vs `<=` — one second before window start."""
        p = _payload(at=datetime(2026, 3, 31, 23, 59, 59, tzinfo=UTC))
        assert not _passes_filters(p, date_from=DEFAULT_DATE_FROM, date_to=DEFAULT_DATE_TO)

    def test_date_at_window_start_accepted(self) -> None:
        """Window is half-open [date_from, date_to) — date_from inclusive."""
        p = _payload(at=DEFAULT_DATE_FROM)
        assert _passes_filters(p, date_from=DEFAULT_DATE_FROM, date_to=DEFAULT_DATE_TO)

    def test_date_at_window_end_rejected(self) -> None:
        """Window is half-open — date_to exclusive."""
        p = _payload(at=DEFAULT_DATE_TO)
        assert not _passes_filters(p, date_from=DEFAULT_DATE_FROM, date_to=DEFAULT_DATE_TO)

    def test_too_short_rejected(self) -> None:
        p = _payload(content="A" * 79)
        assert not _passes_filters(p, date_from=DEFAULT_DATE_FROM, date_to=DEFAULT_DATE_TO)

    def test_at_min_length_accepted(self) -> None:
        p = _payload(content="A" * 80)
        assert _passes_filters(p, date_from=DEFAULT_DATE_FROM, date_to=DEFAULT_DATE_TO)

    def test_too_long_rejected(self) -> None:
        p = _payload(content="A" * 4001)
        assert not _passes_filters(p, date_from=DEFAULT_DATE_FROM, date_to=DEFAULT_DATE_TO)

    def test_at_max_length_accepted(self) -> None:
        p = _payload(content="A" * 4000)
        assert _passes_filters(p, date_from=DEFAULT_DATE_FROM, date_to=DEFAULT_DATE_TO)

    @pytest.mark.parametrize("bad_score", [0, 6, -1, 99])
    def test_score_outside_1_5_rejected(self, bad_score: int) -> None:
        p = _payload(score=bad_score)
        assert not _passes_filters(p, date_from=DEFAULT_DATE_FROM, date_to=DEFAULT_DATE_TO)

    @pytest.mark.parametrize("good_score", [1, 2, 3, 4, 5])
    def test_every_valid_score_accepted(self, good_score: int) -> None:
        p = _payload(score=good_score)
        assert _passes_filters(p, date_from=DEFAULT_DATE_FROM, date_to=DEFAULT_DATE_TO)


# =============================================================================
# Hashing
# =============================================================================


class TestAuthorHash:
    def test_same_salt_same_name_same_hash(self) -> None:
        salt = b"\x00" * 32
        assert author_hash(salt, "alice") == author_hash(salt, "alice")

    def test_different_name_different_hash(self) -> None:
        salt = b"\x00" * 32
        assert author_hash(salt, "alice") != author_hash(salt, "bob")

    def test_different_salt_different_hash(self) -> None:
        assert author_hash(b"\x00" * 32, "alice") != author_hash(b"\x01" * 32, "alice")

    def test_hash_is_64_hex_chars(self) -> None:
        h = author_hash(b"\x00" * 32, "alice")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


class TestLoadOrCreateSalt:
    def test_generates_salt_when_env_missing(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        rng = random.Random(123)
        salt = load_or_create_salt(env, rng=rng)
        assert len(salt) == 32
        assert env.exists()
        assert f"DUOLINGO_REVIEW_SALT={salt.hex()}" in env.read_text(encoding="utf-8")

    def test_reuses_existing_salt(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        known = "aa" * 32
        env.write_text(f"DUOLINGO_REVIEW_SALT={known}\n", encoding="utf-8")
        salt = load_or_create_salt(env, rng=random.Random(999))
        assert salt.hex() == known

    def test_rejects_non_hex_salt(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("DUOLINGO_REVIEW_SALT=not_hex_at_all\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="not valid hex"):
            load_or_create_salt(env)

    def test_rejects_wrong_length_salt(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("DUOLINGO_REVIEW_SALT=aabb\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="bytes, expected 32"):
            load_or_create_salt(env)

    def test_respects_quoted_value(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        known = "cc" * 32
        env.write_text(f'DUOLINGO_REVIEW_SALT="{known}"\n', encoding="utf-8")
        assert load_or_create_salt(env).hex() == known

    def test_skips_comment_lines(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        known = "dd" * 32
        env.write_text(
            f"# DUOLINGO_REVIEW_SALT=ignored_comment\nDUOLINGO_REVIEW_SALT={known}\n",
            encoding="utf-8",
        )
        assert load_or_create_salt(env).hex() == known

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        env = tmp_path / "nested" / "dir" / ".env"
        salt = load_or_create_salt(env, rng=random.Random(0))
        assert env.exists()
        assert len(salt) == 32


# =============================================================================
# Transform
# =============================================================================


class TestToReview:
    def test_review_id_is_deterministic(self) -> None:
        salt = b"\x00" * 32
        p = _payload()
        r1 = to_review(p, salt=salt)
        r2 = to_review(p, salt=salt)
        assert r1.review_id == r2.review_id

    def test_different_timestamp_different_id(self) -> None:
        salt = b"\x00" * 32
        r1 = to_review(_payload(at=datetime(2025, 6, 1, tzinfo=UTC)), salt=salt)
        r2 = to_review(_payload(at=datetime(2025, 6, 2, tzinfo=UTC)), salt=salt)
        assert r1.review_id != r2.review_id

    def test_different_user_different_id(self) -> None:
        salt = b"\x00" * 32
        r1 = to_review(_payload(user="alice"), salt=salt)
        r2 = to_review(_payload(user="bob"), salt=salt)
        assert r1.review_id != r2.review_id

    def test_no_raw_username_in_review(self) -> None:
        """V-09: raw user name must never land on any field."""
        salt = b"\x00" * 32
        r = to_review(_payload(user="alice"), salt=salt)
        dump = r.model_dump_json()
        assert "alice" not in dump

    def test_naive_timestamp_gets_utc(self) -> None:
        salt = b"\x00" * 32
        naive_at = datetime(2025, 6, 1, 12, 0)  # no tzinfo
        r = to_review(_payload(at=naive_at), salt=salt)
        assert r.timestamp_utc.tzinfo is not None


# =============================================================================
# Stratified sampling
# =============================================================================


def _many(n: int, *, score: int, salt: bytes) -> list[Any]:
    """Build N distinct RawReview records with the same score."""
    out: list[Any] = []
    t0 = datetime(2025, 5, 1, tzinfo=UTC)
    for i in range(n):
        p = _payload(user=f"u_{score}_{i}", score=score, at=t0 + timedelta(minutes=i))
        out.append(to_review(p, salt=salt))
    return out


class TestStratifiedSample:
    def test_picks_target_with_right_split(self) -> None:
        salt = b"\x00" * 32
        candidates = _many(500, score=1, salt=salt) + _many(500, score=5, salt=salt)
        picked = stratified_sample(candidates, target=600, seed=42)
        assert len(picked) == 600
        low = sum(1 for r in picked if r.rating in {1, 2, 3})
        high = sum(1 for r in picked if r.rating in {4, 5})
        assert low == 360 and high == 240

    def test_same_seed_same_output(self) -> None:
        salt = b"\x00" * 32
        candidates = _many(500, score=1, salt=salt) + _many(500, score=5, salt=salt)
        a = stratified_sample(candidates, target=600, seed=42)
        b = stratified_sample(candidates, target=600, seed=42)
        assert [r.review_id for r in a] == [r.review_id for r in b]

    def test_different_seed_different_output(self) -> None:
        salt = b"\x00" * 32
        candidates = _many(500, score=1, salt=salt) + _many(500, score=5, salt=salt)
        a = stratified_sample(candidates, target=600, seed=42)
        b = stratified_sample(candidates, target=600, seed=43)
        assert [r.review_id for r in a] != [r.review_id for r in b]

    def test_pilot_is_prefix_of_full_per_stratum(self) -> None:
        """CONTEXT §2: same seed, pilot 60 is a subset of full 600."""
        salt = b"\x00" * 32
        candidates = _many(500, score=1, salt=salt) + _many(500, score=5, salt=salt)
        pilot = {r.review_id for r in stratified_sample(candidates, target=60, seed=42)}
        full = {r.review_id for r in stratified_sample(candidates, target=600, seed=42)}
        assert pilot.issubset(full)

    def test_shortfall_absorbed_by_opposite_bucket(self) -> None:
        """If low-star bucket is thin, high-star fills the gap up to target."""
        salt = b"\x00" * 32
        # 10 low (want 60) + 500 high (want 40) → absorb 50 more from high.
        candidates = _many(10, score=1, salt=salt) + _many(500, score=5, salt=salt)
        picked = stratified_sample(candidates, target=100, seed=42)
        assert len(picked) == 100

    def test_total_candidates_below_target_returns_all(self) -> None:
        salt = b"\x00" * 32
        candidates = _many(30, score=1, salt=salt) + _many(20, score=5, salt=salt)
        picked = stratified_sample(candidates, target=600, seed=42)
        assert len(picked) == 50

    def test_output_is_sorted_by_review_id(self) -> None:
        salt = b"\x00" * 32
        candidates = _many(50, score=1, salt=salt) + _many(50, score=5, salt=salt)
        picked = stratified_sample(candidates, target=30, seed=42)
        ids = [r.review_id for r in picked]
        assert ids == sorted(ids)


# =============================================================================
# IO — atomic write + manifest + idempotency
# =============================================================================


def _fake_fetcher(payloads: list[RawPayload]) -> collect_reviews.Fetcher:
    def _iter() -> Iterator[RawPayload]:
        yield from payloads

    return _iter


class TestIO:
    def test_atomic_write_produces_expected_sha(self, tmp_path: Path) -> None:
        salt = b"\x00" * 32
        records = _many(5, score=2, salt=salt)
        out = tmp_path / "corpus.jsonl"
        sha = _write_corpus_atomic(records, out)
        import hashlib

        assert sha == hashlib.sha256(out.read_bytes()).hexdigest()

    def test_each_line_is_valid_json(self, tmp_path: Path) -> None:
        salt = b"\x00" * 32
        records = _many(3, score=2, salt=salt)
        out = tmp_path / "corpus.jsonl"
        _write_corpus_atomic(records, out)
        for line in out.read_text(encoding="utf-8").splitlines():
            obj = json.loads(line)
            assert set({"review_id", "source", "author_hash", "rating", "text"}).issubset(obj)

    def test_manifest_format_is_sha256sum_compatible(self, tmp_path: Path) -> None:
        salt = b"\x00" * 32
        records = _many(3, score=2, salt=salt)
        out = tmp_path / "corpus.jsonl"
        sha = _write_corpus_atomic(records, out)
        manifest = tmp_path / "corpus.manifest.sha256"
        _write_manifest(out, sha, manifest)
        line = manifest.read_text(encoding="utf-8")
        assert line == f"{sha}  corpus.jsonl\n"

    def test_atomic_write_removes_tmp_on_success(self, tmp_path: Path) -> None:
        salt = b"\x00" * 32
        records = _many(2, score=2, salt=salt)
        out = tmp_path / "corpus.jsonl"
        _write_corpus_atomic(records, out)
        assert not (tmp_path / "corpus.jsonl.tmp").exists()

    def test_read_existing_ids_returns_empty_when_missing(self, tmp_path: Path) -> None:
        assert _read_existing_ids(tmp_path / "nope.jsonl") == set()

    def test_read_existing_ids_round_trip(self, tmp_path: Path) -> None:
        salt = b"\x00" * 32
        records = _many(4, score=2, salt=salt)
        out = tmp_path / "corpus.jsonl"
        _write_corpus_atomic(records, out)
        assert _read_existing_ids(out) == {r.review_id for r in records}

    def test_read_existing_ids_tolerates_malformed_tail(self, tmp_path: Path) -> None:
        """A partially-written final line must not crash dedup."""
        out = tmp_path / "corpus.jsonl"
        out.write_text(
            json.dumps({"review_id": "aaa", "x": 1}) + "\n" + "{not json\n",
            encoding="utf-8",
        )
        assert _read_existing_ids(out) == {"aaa"}

    def test_read_existing_records_returns_empty_when_missing(self, tmp_path: Path) -> None:
        assert _read_existing_records(tmp_path / "nope.jsonl") == []

    def test_read_existing_records_round_trip(self, tmp_path: Path) -> None:
        salt = b"\x00" * 32
        records = _many(4, score=2, salt=salt)
        out = tmp_path / "corpus.jsonl"
        _write_corpus_atomic(records, out)
        got = _read_existing_records(out)
        assert [r.review_id for r in got] == [r.review_id for r in records]

    def test_read_existing_records_tolerates_malformed_tail(self, tmp_path: Path) -> None:
        """B-01 hinges on this: a half-written tail line must not eat prior records.

        Parallel to `test_read_existing_ids_tolerates_malformed_tail` but for the
        pydantic-parsing path — if the `except` tuple ever narrows, this catches it.
        """
        salt = b"\x00" * 32
        good = _many(1, score=2, salt=salt)[0]
        out = tmp_path / "corpus.jsonl"
        out.write_text(
            good.model_dump_json() + "\n" + "{not json\n",
            encoding="utf-8",
        )
        records = _read_existing_records(out)
        assert [r.review_id for r in records] == [good.review_id]

    def test_read_existing_records_drops_schema_incompatible_line(self, tmp_path: Path) -> None:
        """A JSON-valid but schema-incompatible line (e.g. missing `lang`) is dropped."""
        salt = b"\x00" * 32
        good = _many(1, score=2, salt=salt)[0]
        out = tmp_path / "corpus.jsonl"
        out.write_text(
            good.model_dump_json() + "\n" + json.dumps({"review_id": "bad", "x": 1}) + "\n",
            encoding="utf-8",
        )
        records = _read_existing_records(out)
        assert [r.review_id for r in records] == [good.review_id]


# =============================================================================
# Orchestration — collect()
# =============================================================================


class TestCollect:
    def test_end_to_end_filters_and_samples(self) -> None:
        salt = b"\x00" * 32
        payloads: list[RawPayload] = []
        for i in range(20):
            payloads.append(_payload(user=f"low_{i}", score=1, ext_id=f"gl_{i}"))
        for i in range(20):
            payloads.append(_payload(user=f"high_{i}", score=5, ext_id=f"gh_{i}"))
        # Inject noise that should be filtered.
        payloads.append(_payload(user="too_short", content="x" * 10, ext_id="ns"))
        payloads.append(_payload(user="old", at=datetime(2024, 1, 1, tzinfo=UTC), ext_id="no"))
        picked = collect(
            fetcher=_fake_fetcher(payloads),
            salt=salt,
            target=10,
        )
        assert len(picked) == 10
        # CONTEXT §2 stratification: target=10 with LOW_RATIO=0.60 → 6 / 4.
        low = sum(1 for r in picked if r.rating in {1, 2, 3})
        high = sum(1 for r in picked if r.rating in {4, 5})
        assert (low, high) == (6, 4)
        # Noise did not slip through — all survivors are in the window & long enough.
        for r in picked:
            assert len(r.text) >= 80
            assert r.timestamp_utc >= DEFAULT_DATE_FROM

    def test_idempotency_against_existing_ids(self) -> None:
        salt = b"\x00" * 32
        payloads = [_payload(user=f"u_{i}", score=2, ext_id=f"e_{i}") for i in range(10)]
        first = collect(fetcher=_fake_fetcher(payloads), salt=salt, target=100)
        assert len(first) == 10
        second = collect(
            fetcher=_fake_fetcher(payloads),
            salt=salt,
            target=100,
            existing_ids={r.review_id for r in first},
        )
        assert second == []

    def test_determinism_same_seed(self) -> None:
        salt = b"\x00" * 32
        payloads = []
        for i in range(50):
            payloads.append(_payload(user=f"low_{i}", score=1, ext_id=f"l_{i}"))
            payloads.append(_payload(user=f"high_{i}", score=5, ext_id=f"h_{i}"))
        a = collect(fetcher=_fake_fetcher(payloads), salt=salt, target=30, seed=42)
        b = collect(fetcher=_fake_fetcher(payloads), salt=salt, target=30, seed=42)
        assert [r.review_id for r in a] == [r.review_id for r in b]


# =============================================================================
# CLI — main()
# =============================================================================


def _stratified_payloads(prefix: str, n: int, *, low_share: int) -> list[RawPayload]:
    """Build N payloads — `low_share` of them 1-star, the rest 5-star."""
    out: list[RawPayload] = []
    for i in range(n):
        out.append(
            _payload(
                user=f"{prefix}_{i}",
                score=1 if i < low_share else 5,
                ext_id=f"{prefix}_{i}",
            )
        )
    return out


class TestMain:
    def test_writes_corpus_and_manifest(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """End-to-end: --target N produces N records + a sha256 manifest."""
        env = tmp_path / ".env"
        out = tmp_path / "corpus.jsonl"
        payloads = _stratified_payloads("e", 20, low_share=12)

        def _factory(*_a: Any, **_k: Any) -> collect_reviews.Fetcher:
            return _fake_fetcher(payloads)

        monkeypatch.setattr(collect_reviews, "google_play_fetcher", _factory)

        rc = collect_reviews.main(
            [
                "--target",
                "10",
                "--out-path",
                str(out),
                "--env-path",
                str(env),
            ]
        )
        assert rc == 0
        assert out.exists()
        lines = out.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 10
        for line in lines:
            obj = json.loads(line)
            assert set({"review_id", "source", "author_hash", "rating", "text"}).issubset(obj)

        manifest = tmp_path / "corpus.manifest.sha256"
        assert manifest.exists()
        import hashlib

        sha = hashlib.sha256(out.read_bytes()).hexdigest()
        assert manifest.read_text(encoding="utf-8") == f"{sha}  corpus.jsonl\n"

    def test_rerun_preserves_existing_and_adds_new(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression guard for B-01.

        A rerun against a disjoint payload set must:
        (a) keep every `review_id` from the first run, and
        (b) top up the corpus toward `--target`, never exceed it.
        """
        env = tmp_path / ".env"
        out = tmp_path / "corpus.jsonl"

        # First run — 10 records.
        payloads_a = _stratified_payloads("a", 20, low_share=12)
        monkeypatch.setattr(
            collect_reviews,
            "google_play_fetcher",
            lambda *_a, **_k: _fake_fetcher(payloads_a),
        )
        assert (
            collect_reviews.main(
                [
                    "--target",
                    "10",
                    "--out-path",
                    str(out),
                    "--env-path",
                    str(env),
                ]
            )
            == 0
        )
        first_ids = {json.loads(line)["review_id"] for line in out.read_text(encoding="utf-8").splitlines()}
        assert len(first_ids) == 10

        # Second run — disjoint payloads, larger target. Must preserve + top up.
        payloads_b = _stratified_payloads("b", 20, low_share=12)
        monkeypatch.setattr(
            collect_reviews,
            "google_play_fetcher",
            lambda *_a, **_k: _fake_fetcher(payloads_b),
        )
        assert (
            collect_reviews.main(
                [
                    "--target",
                    "20",
                    "--out-path",
                    str(out),
                    "--env-path",
                    str(env),
                ]
            )
            == 0
        )
        second_ids = {json.loads(line)["review_id"] for line in out.read_text(encoding="utf-8").splitlines()}

        # (a) preservation — every first-run id survives.
        assert first_ids.issubset(second_ids), "rerun destroyed previously-captured records"
        # (b) convergence — total equals target, not 2×target.
        assert len(second_ids) == 20

    def test_rerun_at_target_is_a_noop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the corpus already has --target records, rerun adds nothing."""
        env = tmp_path / ".env"
        out = tmp_path / "corpus.jsonl"
        payloads = _stratified_payloads("c", 20, low_share=12)
        monkeypatch.setattr(
            collect_reviews,
            "google_play_fetcher",
            lambda *_a, **_k: _fake_fetcher(payloads),
        )
        assert (
            collect_reviews.main(
                [
                    "--target",
                    "10",
                    "--out-path",
                    str(out),
                    "--env-path",
                    str(env),
                ]
            )
            == 0
        )
        sha_before = (tmp_path / "corpus.manifest.sha256").read_text(encoding="utf-8")
        first = out.read_text(encoding="utf-8")

        # Rerun with the same target; disjoint payloads shouldn't matter.
        payloads_other = _stratified_payloads("d", 20, low_share=12)
        monkeypatch.setattr(
            collect_reviews,
            "google_play_fetcher",
            lambda *_a, **_k: _fake_fetcher(payloads_other),
        )
        assert (
            collect_reviews.main(
                [
                    "--target",
                    "10",
                    "--out-path",
                    str(out),
                    "--env-path",
                    str(env),
                ]
            )
            == 0
        )
        assert out.read_text(encoding="utf-8") == first
        assert (tmp_path / "corpus.manifest.sha256").read_text(encoding="utf-8") == sha_before
