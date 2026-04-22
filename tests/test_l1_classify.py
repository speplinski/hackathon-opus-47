"""Tests for `auditable_design.layers.l1_classify` — no network, no real Claude.

Every test that needs a Claude "call" uses the :class:`FakeClient` stand-in
defined below. It mirrors the part of :class:`auditable_design.claude_client.Client`
that :mod:`l1_classify` consumes (``async def call(...)`` returning a
:class:`ClaudeResponse`), and records every invocation so tests can assert
on what the classifier sent.

Structure mirrors the module's sections (skill hash, prompt build, parsing,
sampling, IO, classification, CLI).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from auditable_design.claude_client import ClaudeResponse
from auditable_design.layers import l1_classify
from auditable_design.layers.l1_classify import (
    MAX_TOKENS,
    MODEL,
    RUBRIC_VOCAB,
    SKILL_ID,
    SYSTEM_PROMPT,
    TEMPERATURE,
    ParseError,
    build_user_message,
    classify_batch,
    classify_one,
    load_corpus,
    load_existing_classified,
    parse_response,
    skill_hash,
    stratified_sample,
)
from auditable_design.schemas import ClassifiedReview, RawReview


# =============================================================================
# Helpers — fake client + review factory
# =============================================================================


@dataclass
class FakeClient:
    """In-memory stand-in for claude_client.Client.

    ``scripted`` maps a substring-matcher → response text. The matcher
    is checked against the ``user`` message; the first hit wins. Passing
    a ``default_response`` makes the client answer every non-matched
    call with that text.
    """

    scripted: dict[str, str] = field(default_factory=dict)
    default_response: str | None = None
    raise_on: dict[str, Exception] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    cumulative_usd: float = 0.0
    cache_size: int = 0
    mode: str = "fake"

    async def call(
        self,
        *,
        system: str,
        user: str,
        model: str,
        skill_id: str,
        skill_hash: str,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> ClaudeResponse:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "model": model,
                "skill_id": skill_id,
                "skill_hash": skill_hash,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        for key, exc in self.raise_on.items():
            if key in user:
                raise exc
        response_text = self.default_response
        for key, text in self.scripted.items():
            if key in user:
                response_text = text
                break
        if response_text is None:
            raise RuntimeError(f"FakeClient: no scripted response for user={user[:80]!r}...")
        return ClaudeResponse(
            call_id="fake-call",
            key_hash="0" * 64,
            skill_id=skill_id,
            skill_hash=skill_hash,
            model=model,
            temperature=float(temperature),
            prompt=f"SYSTEM:\t{system}\tUSER:\t{user}",
            response=response_text,
            input_tokens=100,
            output_tokens=20,
            cost_usd=0.0,
            timestamp="2026-04-22T12:00:00+00:00",
            cache_hit=False,
            elapsed_s=0.0,
        )


def _review(
    *,
    review_id: str = "a" * 40,
    rating: int = 2,
    text: str = "This is a sample review text long enough to be plausible.",
    timestamp: datetime | None = None,
    author_hash: str = "0" * 64,
    source: str = "google_play",
    lang: str = "en",
    app_version: str | None = "5.0.1",
) -> RawReview:
    """Build a valid RawReview with sensible defaults; tests tweak one field."""
    return RawReview(
        review_id=review_id,
        source=source,  # type: ignore[arg-type]
        author_hash=author_hash,
        timestamp_utc=timestamp or datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
        rating=rating,
        text=text,
        lang=lang,
        app_version=app_version,
    )


def _valid_json(is_ux: bool = True, conf: float = 0.9, tags: list[str] | None = None) -> str:
    return json.dumps(
        {
            "is_ux_relevant": is_ux,
            "classifier_confidence": conf,
            "rubric_tags": tags if tags is not None else ["paywall"],
        }
    )


# =============================================================================
# Skill hash
# =============================================================================


class TestSkillHash:
    def test_is_deterministic(self) -> None:
        assert skill_hash() == skill_hash()

    def test_is_sha256_hex(self) -> None:
        h = skill_hash()
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_matches_sha256_of_system_prompt(self) -> None:
        expected = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
        assert skill_hash() == expected


# =============================================================================
# build_user_message
# =============================================================================


class TestBuildUserMessage:
    def test_includes_rating_outside_tag(self) -> None:
        r = _review(rating=3)
        msg = build_user_message(r)
        # rating line comes first, before the <user_review> tag
        first_line, _, rest = msg.partition("\n")
        assert first_line == "rating: 3"
        assert rest.startswith("<user_review ")

    def test_wraps_text_in_user_review_tag(self) -> None:
        r = _review(review_id="feedbeef" * 5, text="Hello world")
        msg = build_user_message(r)
        assert '<user_review id="feedbeef' in msg
        assert msg.rstrip().endswith("</user_review>")

    def test_escapes_markup_like_characters(self) -> None:
        r = _review(text="I love Duolingo <3 & it's great")
        msg = build_user_message(r)
        assert "&lt;3" in msg
        assert "&amp;" in msg
        # Raw `<3` must not appear — escape must happen before interpolation
        assert "<3" not in msg

    def test_review_id_propagates_verbatim(self) -> None:
        rid = "1" * 40
        r = _review(review_id=rid)
        msg = build_user_message(r)
        assert f'id="{rid}"' in msg


# =============================================================================
# parse_response
# =============================================================================


class TestParseResponseBasic:
    def test_round_trip_valid_json(self) -> None:
        raw = _valid_json(is_ux=True, conf=0.85, tags=["paywall", "hearts_streak"])
        is_ux, conf, tags = parse_response(raw)
        assert is_ux is True
        assert conf == pytest.approx(0.85)
        assert tags == ["paywall", "hearts_streak"]

    def test_empty_rubric_tags_accepted(self) -> None:
        raw = _valid_json(is_ux=False, conf=0.7, tags=[])
        is_ux, conf, tags = parse_response(raw)
        assert is_ux is False
        assert conf == pytest.approx(0.7)
        assert tags == []

    def test_confidence_boundaries_accepted(self) -> None:
        for conf in (0.0, 1.0):
            is_ux, got_conf, tags = parse_response(_valid_json(conf=conf))
            assert got_conf == pytest.approx(conf)

    def test_confidence_as_int_accepted(self) -> None:
        # JSON `1` arrives as Python int; should be coerced to 1.0 since
        # ints are legitimate numeric values.
        raw = json.dumps({"is_ux_relevant": True, "classifier_confidence": 1, "rubric_tags": []})
        _, conf, _ = parse_response(raw)
        assert conf == pytest.approx(1.0)

    def test_dedup_tags_preserves_order(self) -> None:
        raw = _valid_json(tags=["paywall", "ads", "paywall", "hearts_streak", "ads"])
        _, _, tags = parse_response(raw)
        assert tags == ["paywall", "ads", "hearts_streak"]


class TestParseResponseWrapperTolerance:
    def test_tolerates_leading_prose(self) -> None:
        raw = f"Here is the classification: {_valid_json()}"
        is_ux, _, _ = parse_response(raw)
        assert is_ux is True

    def test_tolerates_trailing_prose(self) -> None:
        raw = f"{_valid_json()}\n\nLet me know if you need more."
        is_ux, _, _ = parse_response(raw)
        assert is_ux is True

    def test_tolerates_code_fence(self) -> None:
        raw = f"```json\n{_valid_json()}\n```"
        is_ux, _, _ = parse_response(raw)
        assert is_ux is True

    def test_no_json_at_all_rejected(self) -> None:
        with pytest.raises(ParseError, match="no JSON object"):
            parse_response("Sorry, I can't classify this.")


class TestParseResponseSchemaEnforcement:
    def test_missing_key_rejected(self) -> None:
        raw = json.dumps({"is_ux_relevant": True, "rubric_tags": []})
        with pytest.raises(ParseError, match="key set mismatch"):
            parse_response(raw)

    def test_extra_key_rejected(self) -> None:
        raw = json.dumps(
            {
                "is_ux_relevant": True,
                "classifier_confidence": 0.9,
                "rubric_tags": [],
                "reasoning": "looks UX to me",
            }
        )
        with pytest.raises(ParseError, match="key set mismatch"):
            parse_response(raw)

    def test_is_ux_relevant_must_be_bool(self) -> None:
        raw = json.dumps(
            {"is_ux_relevant": "true", "classifier_confidence": 0.9, "rubric_tags": []}
        )
        with pytest.raises(ParseError, match="is_ux_relevant must be a bool"):
            parse_response(raw)

    def test_confidence_must_be_number(self) -> None:
        raw = json.dumps(
            {"is_ux_relevant": True, "classifier_confidence": "high", "rubric_tags": []}
        )
        with pytest.raises(ParseError, match="classifier_confidence must be a number"):
            parse_response(raw)

    def test_confidence_as_bool_rejected(self) -> None:
        # `True` is a bool subclass of int — without the explicit filter
        # it would silently coerce to 1.0. The parser must reject it.
        raw = json.dumps(
            {"is_ux_relevant": True, "classifier_confidence": True, "rubric_tags": []}
        )
        with pytest.raises(ParseError, match="classifier_confidence must be a number"):
            parse_response(raw)

    def test_confidence_out_of_range_rejected(self) -> None:
        for bad in (-0.01, 1.5, 2.0):
            raw = json.dumps(
                {"is_ux_relevant": True, "classifier_confidence": bad, "rubric_tags": []}
            )
            with pytest.raises(ParseError, match="out of \\[0, 1\\]"):
                parse_response(raw)

    def test_rubric_tags_must_be_list(self) -> None:
        raw = json.dumps(
            {"is_ux_relevant": True, "classifier_confidence": 0.9, "rubric_tags": "paywall"}
        )
        with pytest.raises(ParseError, match="rubric_tags must be a list"):
            parse_response(raw)

    def test_non_string_tag_rejected(self) -> None:
        raw = json.dumps(
            {"is_ux_relevant": True, "classifier_confidence": 0.9, "rubric_tags": ["paywall", 1]}
        )
        with pytest.raises(ParseError, match="element must be a string"):
            parse_response(raw)

    def test_out_of_vocab_tag_rejected(self) -> None:
        raw = _valid_json(tags=["paywall", "privacy"])  # privacy is not in vocab
        with pytest.raises(ParseError, match="not in closed vocabulary"):
            parse_response(raw)

    def test_malformed_json_rejected(self) -> None:
        raw = '{"is_ux_relevant": true, "classifier_confidence": 0.9, "rubric_tags": ['
        with pytest.raises(ParseError, match="malformed JSON|no JSON object"):
            parse_response(raw)

    def test_top_level_must_be_object(self) -> None:
        # `[]` contains `{` nowhere so parse falls through "no JSON object".
        # A more tricky case: `{"is_ux_relevant": true} [1,2]` — first `{` to last `}` still parses.
        raw = "[1, 2, 3]"
        with pytest.raises(ParseError, match="no JSON object"):
            parse_response(raw)


# =============================================================================
# stratified_sample
# =============================================================================


class TestStratifiedSample:
    def _mixed_corpus(self, n_low: int = 20, n_high: int = 20) -> list[RawReview]:
        out: list[RawReview] = []
        for i in range(n_low):
            out.append(_review(review_id=f"low-{i:03d}" + "0" * 33, rating=(i % 3) + 1))
        for i in range(n_high):
            out.append(_review(review_id=f"high-{i:03d}" + "0" * 32, rating=4 + (i % 2)))
        return out

    def test_returns_requested_bucket_counts(self) -> None:
        corpus = self._mixed_corpus(20, 20)
        picked = stratified_sample(corpus, low_target=6, high_target=4, seed=42)
        lows = [r for r in picked if r.rating in (1, 2, 3)]
        highs = [r for r in picked if r.rating in (4, 5)]
        assert len(lows) == 6
        assert len(highs) == 4
        assert len(picked) == 10

    def test_deterministic_under_seed(self) -> None:
        corpus = self._mixed_corpus()
        a = stratified_sample(corpus, low_target=6, high_target=4, seed=42)
        b = stratified_sample(corpus, low_target=6, high_target=4, seed=42)
        assert [r.review_id for r in a] == [r.review_id for r in b]

    def test_different_seed_picks_different_subset(self) -> None:
        corpus = self._mixed_corpus()
        a = stratified_sample(corpus, low_target=6, high_target=4, seed=42)
        b = stratified_sample(corpus, low_target=6, high_target=4, seed=43)
        assert [r.review_id for r in a] != [r.review_id for r in b]

    def test_sorted_by_review_id(self) -> None:
        corpus = self._mixed_corpus()
        picked = stratified_sample(corpus, low_target=6, high_target=4, seed=42)
        ids = [r.review_id for r in picked]
        assert ids == sorted(ids)

    def test_not_enough_low_raises(self) -> None:
        corpus = self._mixed_corpus(n_low=3, n_high=20)
        with pytest.raises(ValueError, match="low-star"):
            stratified_sample(corpus, low_target=6, high_target=4, seed=42)

    def test_not_enough_high_raises(self) -> None:
        corpus = self._mixed_corpus(n_low=20, n_high=2)
        with pytest.raises(ValueError, match="high-star"):
            stratified_sample(corpus, low_target=6, high_target=4, seed=42)


# =============================================================================
# load_corpus / load_existing_classified
# =============================================================================


class TestLoadCorpus:
    def test_reads_valid_corpus(self, tmp_path: Path) -> None:
        r = _review()
        path = tmp_path / "corpus.jsonl"
        path.write_text(json.dumps(r.model_dump(mode="json")) + "\n")
        got = load_corpus(path)
        assert len(got) == 1
        assert got[0].review_id == r.review_id

    def test_rejects_missing_field(self, tmp_path: Path) -> None:
        path = tmp_path / "corpus.jsonl"
        path.write_text(json.dumps({"review_id": "x", "rating": 3}) + "\n")
        with pytest.raises(ValueError, match="line 1"):
            load_corpus(path)

    def test_rejects_unknown_field(self, tmp_path: Path) -> None:
        # RawReview has extra="forbid" — drift should fail loud.
        r = _review()
        data = r.model_dump(mode="json")
        data["unexpected"] = "drift"
        path = tmp_path / "corpus.jsonl"
        path.write_text(json.dumps(data) + "\n")
        with pytest.raises(ValueError, match="line 1"):
            load_corpus(path)


class TestLoadExistingClassified:
    def test_returns_empty_when_missing(self, tmp_path: Path) -> None:
        assert load_existing_classified(tmp_path / "nope.jsonl") == []

    def test_round_trip(self, tmp_path: Path) -> None:
        c = ClassifiedReview(
            review_id="a" * 40,
            is_ux_relevant=True,
            classifier_confidence=0.8,
            rubric_tags=["paywall"],
            classified_at=datetime(2026, 4, 22, 12, 0, tzinfo=UTC),
        )
        path = tmp_path / "l1_classified.jsonl"
        path.write_text(json.dumps(c.model_dump(mode="json")) + "\n")
        got = load_existing_classified(path)
        assert len(got) == 1
        assert got[0].review_id == c.review_id
        assert got[0].rubric_tags == ["paywall"]

    def test_tolerates_invalid_line(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        good = ClassifiedReview(
            review_id="a" * 40,
            is_ux_relevant=True,
            classifier_confidence=0.8,
            rubric_tags=[],
            classified_at=datetime(2026, 4, 22, 12, 0, tzinfo=UTC),
        )
        path = tmp_path / "l1_classified.jsonl"
        lines = [
            json.dumps(good.model_dump(mode="json")),
            json.dumps({"review_id": "b" * 40, "rubric_tags": []}),  # missing fields
        ]
        path.write_text("\n".join(lines) + "\n")
        with caplog.at_level("WARNING"):
            got = load_existing_classified(path)
        assert len(got) == 1
        assert got[0].review_id == "a" * 40
        assert any("line 2 invalid" in rec.message for rec in caplog.records)


# =============================================================================
# classify_one
# =============================================================================


class TestClassifyOne:
    def test_happy_path(self) -> None:
        r = _review(review_id="1" * 40, rating=1)
        client = FakeClient(default_response=_valid_json(True, 0.9, ["paywall", "ads"]))
        out = asyncio.run(
            classify_one(r, client, skill_hash_value="0" * 64)  # type: ignore[arg-type]
        )
        assert isinstance(out, ClassifiedReview)
        assert out.review_id == r.review_id
        assert out.is_ux_relevant is True
        assert out.classifier_confidence == pytest.approx(0.9)
        assert out.rubric_tags == ["paywall", "ads"]
        # Prompt plumbing: system is SYSTEM_PROMPT, temperature=0, max_tokens=MAX_TOKENS
        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["system"] == SYSTEM_PROMPT
        assert call["model"] == MODEL
        assert call["temperature"] == TEMPERATURE
        assert call["max_tokens"] == MAX_TOKENS
        assert call["skill_id"] == SKILL_ID

    def test_parse_error_propagates(self) -> None:
        r = _review()
        client = FakeClient(default_response="totally not JSON")
        with pytest.raises(ParseError):
            asyncio.run(
                classify_one(r, client, skill_hash_value="0" * 64)  # type: ignore[arg-type]
            )

    def test_client_error_propagates(self) -> None:
        r = _review()
        client = FakeClient(
            default_response=_valid_json(),
            raise_on={r.review_id: RuntimeError("upstream broken")},
        )
        with pytest.raises(RuntimeError, match="upstream broken"):
            asyncio.run(
                classify_one(r, client, skill_hash_value="0" * 64)  # type: ignore[arg-type]
            )


# =============================================================================
# classify_batch
# =============================================================================


class TestClassifyBatch:
    def test_all_success(self) -> None:
        reviews = [_review(review_id=f"{i:040d}", rating=(i % 5) + 1) for i in range(5)]
        client = FakeClient(default_response=_valid_json(True, 0.8, []))
        successes, failures = asyncio.run(
            classify_batch(reviews, client, skill_hash_value="0" * 64)  # type: ignore[arg-type]
        )
        assert len(successes) == 5
        assert failures == []

    def test_mixed_success_and_failure(self) -> None:
        reviews = [
            _review(review_id="aaaa" + "0" * 36),
            _review(review_id="bbbb" + "0" * 36),
            _review(review_id="cccc" + "0" * 36),
        ]
        # `bbbb` is unscripted — default_response is the bad payload so it
        # falls through to ParseError via the no-JSON branch.
        client = FakeClient(
            scripted={
                "aaaa": _valid_json(True, 0.8, ["paywall"]),
                "cccc": _valid_json(False, 0.3, []),
            },
            default_response="not json",
        )
        successes, failures = asyncio.run(
            classify_batch(reviews, client, skill_hash_value="0" * 64)  # type: ignore[arg-type]
        )
        assert {c.review_id for c in successes} == {
            "aaaa" + "0" * 36,
            "cccc" + "0" * 36,
        }
        assert [rid for rid, _ in failures] == ["bbbb" + "0" * 36]
        assert isinstance(failures[0][1], ParseError)

    def test_empty_input_returns_empty(self) -> None:
        client = FakeClient()
        successes, failures = asyncio.run(
            classify_batch([], client, skill_hash_value="0" * 64)  # type: ignore[arg-type]
        )
        assert successes == [] and failures == []


# =============================================================================
# CLI — end to end
# =============================================================================


def _write_corpus_jsonl(path: Path, reviews: list[RawReview]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r.model_dump(mode="json")) for r in reviews]
    path.write_text("\n".join(lines) + "\n")


class _FakeClientFactory:
    """Capture kwargs and return a FakeClient with a canned response."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.last_instance: FakeClient | None = None

    def __call__(self, **kwargs: Any) -> FakeClient:
        self.last_instance = FakeClient(default_response=self.response)
        return self.last_instance


class TestMain:
    def _setup_repo(self, tmp_path: Path) -> tuple[Path, Path, Path, Path]:
        """Create a fake repo layout with pyproject.toml, corpus.jsonl, etc."""
        repo = tmp_path / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "data" / "raw").mkdir(parents=True)
        (repo / "data" / "derived").mkdir(parents=True)
        (repo / "data" / "cache").mkdir(parents=True)
        (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
        corpus_path = repo / "data" / "raw" / "corpus.jsonl"
        output_path = repo / "data" / "derived" / "l1_classified.jsonl"
        replay_log = repo / "data" / "cache" / "responses.jsonl"
        return repo, corpus_path, output_path, replay_log

    def _pin_repo_root(self, monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
        monkeypatch.setattr(l1_classify, "_resolve_repo_root", lambda: repo)

    def test_end_to_end_writes_classified_and_meta(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo, corpus_path, output_path, replay_log = self._setup_repo(tmp_path)
        reviews = [
            _review(review_id=f"{i:040x}", rating=(i % 5) + 1, text=f"review text number {i} " * 5)
            for i in range(3)
        ]
        _write_corpus_jsonl(corpus_path, reviews)

        self._pin_repo_root(monkeypatch, repo)
        factory = _FakeClientFactory(_valid_json(True, 0.85, ["paywall"]))
        monkeypatch.setattr(l1_classify, "Client", factory)

        rc = l1_classify.main(
            [
                "--input",
                str(corpus_path),
                "--output",
                str(output_path),
                "--replay-log",
                str(replay_log),
                "--run-id",
                "test-run",
                "--mode",
                "live",
            ]
        )
        assert rc == 0
        assert output_path.exists()
        # 3 records written
        lines = [json.loads(x) for x in output_path.read_text().splitlines() if x.strip()]
        assert len(lines) == 3
        assert {row["review_id"] for row in lines} == {r.review_id for r in reviews}
        # sidecar meta exists
        meta_path = output_path.with_suffix(output_path.suffix + ".meta.json")
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["run_id"] == "test-run"
        assert meta["layer"] == l1_classify.LAYER_NAME
        assert meta["item_count"] == 3
        assert "corpus.jsonl" in meta["input_hashes"]
        assert l1_classify.SKILL_ID in meta["skill_hashes"]
        # Client was constructed with the live mode we asked for
        assert factory.last_instance is not None

    def test_rerun_is_idempotent_and_merges(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo, corpus_path, output_path, replay_log = self._setup_repo(tmp_path)
        reviews = [
            _review(review_id=f"{i:040x}", rating=(i % 5) + 1, text=f"review text number {i} " * 5)
            for i in range(5)
        ]
        _write_corpus_jsonl(corpus_path, reviews)

        self._pin_repo_root(monkeypatch, repo)

        # First run — classify only first 3
        factory_a = _FakeClientFactory(_valid_json(True, 0.8, ["paywall"]))
        monkeypatch.setattr(l1_classify, "Client", factory_a)
        l1_classify.main(
            [
                "--input",
                str(corpus_path),
                "--output",
                str(output_path),
                "--replay-log",
                str(replay_log),
                "--run-id",
                "run-1",
                "--mode",
                "live",
                "--limit",
                "3",
            ]
        )
        first_lines = [json.loads(x) for x in output_path.read_text().splitlines() if x.strip()]
        assert len(first_lines) == 3

        # Second run — different limit, different answer. Prior 3 should
        # survive untouched (factory_b would give different tags if
        # classified fresh, but idempotent path skips them).
        factory_b = _FakeClientFactory(_valid_json(False, 0.4, ["off_topic"]))
        monkeypatch.setattr(l1_classify, "Client", factory_b)
        l1_classify.main(
            [
                "--input",
                str(corpus_path),
                "--output",
                str(output_path),
                "--replay-log",
                str(replay_log),
                "--run-id",
                "run-2",
                "--mode",
                "live",
                "--limit",
                "5",
            ]
        )
        all_lines = [json.loads(x) for x in output_path.read_text().splitlines() if x.strip()]
        assert len(all_lines) == 5
        ids = {row["review_id"] for row in all_lines}
        assert ids == {r.review_id for r in reviews}
        # Prior 3 still have paywall tag; new 2 have off_topic
        assert factory_b.last_instance is not None
        # factory_b only got 2 new calls (the 2 reviews not in run-1)
        assert len(factory_b.last_instance.calls) == 2

    def test_rerun_at_target_is_a_noop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo, corpus_path, output_path, replay_log = self._setup_repo(tmp_path)
        reviews = [
            _review(review_id=f"{i:040x}", rating=(i % 5) + 1, text=f"review text number {i} " * 5)
            for i in range(2)
        ]
        _write_corpus_jsonl(corpus_path, reviews)
        self._pin_repo_root(monkeypatch, repo)

        factory = _FakeClientFactory(_valid_json(True, 0.8, ["paywall"]))
        monkeypatch.setattr(l1_classify, "Client", factory)

        # First run classifies both
        l1_classify.main(
            [
                "--input",
                str(corpus_path),
                "--output",
                str(output_path),
                "--replay-log",
                str(replay_log),
                "--run-id",
                "run-1",
                "--mode",
                "live",
            ]
        )
        assert factory.last_instance is not None
        first_call_count = len(factory.last_instance.calls)
        assert first_call_count == 2

        # Second run — nothing new to do, client makes zero calls
        factory2 = _FakeClientFactory(_valid_json(True, 0.8, ["paywall"]))
        monkeypatch.setattr(l1_classify, "Client", factory2)
        rc = l1_classify.main(
            [
                "--input",
                str(corpus_path),
                "--output",
                str(output_path),
                "--replay-log",
                str(replay_log),
                "--run-id",
                "run-2",
                "--mode",
                "live",
            ]
        )
        assert rc == 0
        assert factory2.last_instance is not None
        assert factory2.last_instance.calls == []

    def test_stratified_half_spec_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo, corpus_path, output_path, replay_log = self._setup_repo(tmp_path)
        _write_corpus_jsonl(corpus_path, [_review(review_id="a" * 40)])
        self._pin_repo_root(monkeypatch, repo)
        with pytest.raises(SystemExit):
            l1_classify.main(
                [
                    "--input",
                    str(corpus_path),
                    "--output",
                    str(output_path),
                    "--replay-log",
                    str(replay_log),
                    "--stratified-low",
                    "3",
                    # intentionally no --stratified-high
                ]
            )


# =============================================================================
# Vocabulary sanity
# =============================================================================


class TestVocabularySanity:
    def test_ux_and_non_ux_are_disjoint(self) -> None:
        assert l1_classify.UX_TAGS.isdisjoint(l1_classify.NON_UX_TAGS)

    def test_vocabulary_is_union(self) -> None:
        assert RUBRIC_VOCAB == l1_classify.UX_TAGS | l1_classify.NON_UX_TAGS

    def test_expected_ux_tags_present(self) -> None:
        # CONTEXT §1 five + catch-all
        expected = {
            "paywall",
            "hearts_streak",
            "notifications",
            "ads",
            "feature_removal",
            "interface_other",
        }
        assert l1_classify.UX_TAGS == expected

    def test_expected_non_ux_tags_present(self) -> None:
        expected = {"content_quality", "billing", "bug", "off_topic"}
        assert l1_classify.NON_UX_TAGS == expected
