"""Tests for ``auditable_design.layers.l3b_label``.

Structure mirrors the layer module:
constants → skill_hash → build_user_message → parse_label_response →
label_cluster / label_batch → merge_outcomes → build_provenance → CLI.

Strategy
--------
Every test that exercises Claude uses an in-process :class:`FakeClient`
with scripted responses — same pattern as ``test_l2_structure.py`` so a
reviewer who has read one test module already understands the other.
No network, no real replay log, no sentence-transformers load; the
whole file runs in <1s.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from auditable_design.claude_client import ClaudeResponse
from auditable_design.layers import l3b_label
from auditable_design.layers.l3b_label import (
    DEFAULT_CLUSTERS,
    DEFAULT_LABELED,
    LABEL_MAX_LEN,
    LABEL_MIN_LEN,
    LAYER_NAME,
    MAX_TOKENS,
    MIXED_LABEL,
    MODEL,
    SKILL_ID,
    SYSTEM_PROMPT,
    TEMPERATURE,
    LabelOutcome,
    LabelParseError,
    build_provenance,
    build_user_message,
    label_batch,
    label_cluster,
    load_clusters,
    main,
    merge_outcomes,
    parse_label_response,
    skill_hash,
)
from auditable_design.schemas import InsightCluster


# =============================================================================
# Helpers
# =============================================================================


@dataclass
class FakeClient:
    """In-memory stand-in for claude_client.Client.

    Same shape as the L2 test's FakeClient, intentionally duplicated so
    a change in one test module doesn't silently affect the other.
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
            raise RuntimeError(
                f"FakeClient: no scripted response for user={user[:80]!r}..."
            )
        return ClaudeResponse(
            call_id="fake-call",
            key_hash="0" * 64,
            skill_id=skill_id,
            skill_hash=skill_hash,
            model=model,
            temperature=float(temperature),
            prompt=f"SYSTEM:\t{system}\tUSER:\t{user}",
            response=response_text,
            input_tokens=50,
            output_tokens=10,
            cost_usd=0.0,
            timestamp="2026-04-22T12:00:00+00:00",
            cache_hit=False,
            elapsed_s=0.0,
        )


def _cluster(
    *,
    cluster_id: str = "cluster_00",
    label: str | None = None,
    quotes: list[str] | None = None,
    members: list[str] | None = None,
    centroid_ref: str = "l3_centroids.npy#0",
) -> InsightCluster:
    """Build an InsightCluster with sensible defaults.

    Defaults produce a coherent "voice recognition" cluster — matches
    the worked example in SKILL.md so test-time readers can eyeball the
    shape against the skill documentation.
    """
    return InsightCluster(
        cluster_id=cluster_id,
        label=label if label is not None else f"UNLABELED:{cluster_id}",
        member_review_ids=members or ["r1", "r2", "r3"],
        centroid_vector_ref=centroid_ref,
        representative_quotes=quotes
        or [
            "I am speaking but it says wrong",
            "I keep getting it wrong",
            "give me wrong answers",
        ],
    )


def _label_json(label: str) -> str:
    return json.dumps({"label": label})


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for r in rows
        )
        + ("\n" if rows else "")
    )


# =============================================================================
# Constants — sanity
# =============================================================================


class TestConstants:
    def test_skill_id(self) -> None:
        assert SKILL_ID == "label-cluster"

    def test_layer_name(self) -> None:
        assert LAYER_NAME == "l3b_label"

    def test_label_bounds(self) -> None:
        # Matches the SKILL.md contract — if these drift, SKILL.md and
        # the layer are disagreeing, and the failure message names the
        # mismatch explicitly instead of a cryptic parse rejection at
        # runtime.
        assert LABEL_MIN_LEN == 1
        assert LABEL_MAX_LEN == 60

    def test_mixed_label_sentinel(self) -> None:
        # L4 cluster-coherence audit will key on this exact string.
        # If it changes, the L4 audit's keying must change too —
        # failing here catches the drift at CI time.
        assert MIXED_LABEL == "Mixed complaints"

    def test_default_model_is_haiku(self) -> None:
        # Labelling is a short bounded transformation; Opus premium
        # is unjustified. If the default shifts to Opus, there should
        # be an ADR explaining why.
        assert MODEL == "claude-haiku-4-5-20251001"

    def test_temperature_is_zero(self) -> None:
        assert TEMPERATURE == 0.0

    def test_max_tokens_fits_response(self) -> None:
        # 512 was chosen to give Opus 4.6 room for a short reasoning
        # preamble BEFORE the JSON — rubric v2 showed it occasionally
        # hits 128 mid-thought without ever emitting ``{...}``, which
        # causes UNLABELED fallbacks. If someone bumps LABEL_MAX_LEN,
        # they must revisit this bound too. Lowering back to 128 will
        # re-introduce the Opus 4.6 parse-fail regression on reasoning-
        # first clusters.
        assert MAX_TOKENS == 512

    def test_default_paths(self) -> None:
        assert DEFAULT_CLUSTERS == Path("data/derived/l3_clusters.jsonl")
        assert DEFAULT_LABELED == Path("data/derived/l3b_labeled_clusters.jsonl")


# =============================================================================
# skill_hash — identity of the brain
# =============================================================================


class TestSkillHash:
    def test_is_sha256_hex(self) -> None:
        h = skill_hash()
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self) -> None:
        assert skill_hash() == skill_hash()

    def test_derived_from_system_prompt(self) -> None:
        # Belt-and-suspenders: the constant MUST be the hash of
        # SYSTEM_PROMPT, not a hand-coded value. If someone pastes a
        # literal digest here by mistake, this test catches it.
        import hashlib

        expected = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
        assert skill_hash() == expected


# =============================================================================
# build_user_message — shape + injection escape
# =============================================================================


class TestBuildUserMessage:
    def test_wraps_in_cluster_quotes(self) -> None:
        msg = build_user_message(["foo", "bar"])
        assert msg.startswith("<cluster_quotes>")
        assert msg.endswith("</cluster_quotes>")
        assert "<q>foo</q>" in msg
        assert "<q>bar</q>" in msg

    def test_escapes_angle_brackets(self) -> None:
        # A quote that contains ``</q>`` or ``<cluster_quotes>`` could
        # break out of the wrapper and inject instructions. HTML-escape
        # closes that door the same way prompt_builder does for
        # <user_review> wrappers.
        msg = build_user_message(["</q>evil<q>"])
        assert "</q>evil<q>" not in msg
        assert "&lt;/q&gt;evil&lt;q&gt;" in msg

    def test_escapes_ampersand(self) -> None:
        # ``&`` must escape first to avoid double-escaping the entities
        # we already emitted.
        msg = build_user_message(["A & B"])
        assert "A &amp; B" in msg

    def test_preserves_other_content_verbatim(self) -> None:
        quotes = ["I am speaking but it says wrong"]
        msg = build_user_message(quotes)
        assert "I am speaking but it says wrong" in msg

    def test_single_quote_cluster(self) -> None:
        # Degenerate but legal: InsightCluster enforces ≥1 quote.
        msg = build_user_message(["alone"])
        assert msg.count("<q>") == 1


# =============================================================================
# parse_label_response
# =============================================================================


class TestParseLabelResponse:
    def test_happy_path(self) -> None:
        assert parse_label_response(_label_json("Voice recognition")) == "Voice recognition"

    def test_mixed_complaints_sentinel(self) -> None:
        # Must parse cleanly — the skill's first-class incoherence signal.
        assert parse_label_response(_label_json(MIXED_LABEL)) == MIXED_LABEL

    def test_strips_whitespace(self) -> None:
        assert parse_label_response(_label_json("  Paywall  ")) == "Paywall"

    def test_tolerates_prose_around_json(self) -> None:
        # Claude sometimes emits a code-fenced block. The outermost-
        # object regex must recover the JSON.
        text = "Sure! Here is the label:\n```json\n" + _label_json("X") + "\n```"
        assert parse_label_response(text) == "X"

    def test_no_json_object(self) -> None:
        with pytest.raises(LabelParseError, match="no JSON object"):
            parse_label_response("just prose, no json")

    def test_malformed_json(self) -> None:
        # Braces are present so the regex matches, but the contents are
        # not valid JSON (missing colon) — json.loads raises and the
        # parser re-raises with "malformed JSON".
        with pytest.raises(LabelParseError, match="malformed JSON"):
            parse_label_response('{"label" "X"}')

    def test_extra_top_level_keys(self) -> None:
        with pytest.raises(LabelParseError, match="unexpected top-level keys"):
            parse_label_response('{"label": "X", "confidence": 0.9}')

    def test_missing_label_key(self) -> None:
        with pytest.raises(LabelParseError, match="missing required"):
            parse_label_response('{"name": "X"}')

    def test_label_not_string(self) -> None:
        with pytest.raises(LabelParseError, match="must be str"):
            parse_label_response('{"label": 42}')

    def test_empty_label(self) -> None:
        with pytest.raises(LabelParseError, match="length 0"):
            parse_label_response('{"label": ""}')

    def test_whitespace_only_label(self) -> None:
        # Strip first, then length-check — a whitespace-only label
        # should fail as "empty after strip", not "length 3".
        with pytest.raises(LabelParseError, match="length 0"):
            parse_label_response('{"label": "   "}')

    def test_label_too_long(self) -> None:
        long = "x" * (LABEL_MAX_LEN + 1)
        with pytest.raises(LabelParseError, match="> LABEL_MAX_LEN"):
            parse_label_response(_label_json(long))

    def test_echoes_unlabeled_placeholder(self) -> None:
        with pytest.raises(LabelParseError, match="echoes the UNLABELED"):
            parse_label_response(_label_json("UNLABELED:cluster_03"))

    def test_echoes_placeholder_case_insensitive(self) -> None:
        # The sentinel check is case-insensitive to catch wire-drift
        # (``Unlabeled:`` or ``unlabeled:``).
        with pytest.raises(LabelParseError, match="echoes the UNLABELED"):
            parse_label_response(_label_json("unlabeled:cluster_03"))

    def test_label_with_colon_is_not_placeholder(self) -> None:
        # A real label might legitimately contain a colon (e.g.
        # ``"Billing: refund flow"``). Only the ``unlabeled:`` prefix
        # is rejected.
        assert parse_label_response(_label_json("Billing: refund flow")) == "Billing: refund flow"


# =============================================================================
# label_cluster — per-cluster pipeline
# =============================================================================


class TestLabelCluster:
    def test_happy_path(self) -> None:
        cluster = _cluster()
        client = FakeClient(default_response=_label_json("Voice recognition errors"))
        outcome = asyncio.run(
            label_cluster(cluster, client, skill_hash_value="deadbeef" * 8)
        )
        assert outcome.status == "labeled"
        assert outcome.label == "Voice recognition errors"
        assert outcome.cluster_id == "cluster_00"
        assert outcome.reason is None

    def test_parse_failure_falls_back(self) -> None:
        cluster = _cluster(cluster_id="cluster_03")
        # Claude emits prose with no JSON — parse fails, layer falls back.
        client = FakeClient(default_response="I'm not sure what to say.")
        outcome = asyncio.run(
            label_cluster(cluster, client, skill_hash_value="deadbeef" * 8)
        )
        assert outcome.status == "fallback"
        # Placeholder label carried through verbatim.
        assert outcome.label == "UNLABELED:cluster_03"
        assert outcome.reason is not None
        assert "no JSON object" in outcome.reason

    def test_echo_of_placeholder_falls_back(self) -> None:
        # A lazy model that echoes the placeholder must NOT silently
        # round-trip. The parse layer rejects it; L3b records a
        # fallback so the audit trail shows the skill refused to
        # commit to a name.
        cluster = _cluster(cluster_id="cluster_07")
        client = FakeClient(default_response=_label_json("UNLABELED:cluster_07"))
        outcome = asyncio.run(
            label_cluster(cluster, client, skill_hash_value="deadbeef" * 8)
        )
        assert outcome.status == "fallback"
        assert outcome.label == "UNLABELED:cluster_07"
        assert outcome.reason is not None and "echoes the UNLABELED" in outcome.reason

    def test_transport_error_propagates(self) -> None:
        cluster = _cluster()
        client = FakeClient(raise_on={"I am speaking": RuntimeError("network boom")})
        # Transport errors are NOT swallowed into a fallback — they
        # propagate so the caller can decide to abort. ``label_batch``
        # then catches and records them as transport_failures; single-
        # cluster callers see the raw exception.
        with pytest.raises(RuntimeError, match="network boom"):
            asyncio.run(
                label_cluster(cluster, client, skill_hash_value="deadbeef" * 8)
            )

    def test_call_arguments(self) -> None:
        cluster = _cluster()
        client = FakeClient(default_response=_label_json("OK"))
        asyncio.run(label_cluster(cluster, client, skill_hash_value="deadbeef" * 8))
        assert len(client.calls) == 1
        call = client.calls[0]
        # Call params must match the layer's contract — skill_id,
        # temperature, max_tokens are all load-bearing for replay.
        assert call["skill_id"] == SKILL_ID
        assert call["temperature"] == TEMPERATURE
        assert call["max_tokens"] == MAX_TOKENS
        assert call["skill_hash"] == "deadbeef" * 8
        assert call["system"] == SYSTEM_PROMPT
        # The user message is our build_user_message output.
        assert call["user"].startswith("<cluster_quotes>")


# =============================================================================
# label_batch — concurrency + failure sorting
# =============================================================================


class TestLabelBatch:
    def test_all_succeed(self) -> None:
        clusters = [
            _cluster(cluster_id=f"cluster_{i:02d}") for i in range(3)
        ]
        client = FakeClient(default_response=_label_json("Stub label"))
        outcomes, failures = asyncio.run(label_batch(clusters, client))
        assert len(outcomes) == 3
        assert failures == []
        assert all(o.status == "labeled" for o in outcomes)
        assert {o.cluster_id for o in outcomes} == {
            "cluster_00",
            "cluster_01",
            "cluster_02",
        }

    def test_per_cluster_scripting(self) -> None:
        cluster_a = _cluster(
            cluster_id="cluster_00",
            quotes=["freezing", "freezes"],
        )
        cluster_b = _cluster(
            cluster_id="cluster_01",
            quotes=["expensive", "cost too much"],
        )
        client = FakeClient(
            scripted={
                "freezing": _label_json("App freezes"),
                "expensive": _label_json("Paywall cost"),
            }
        )
        outcomes, failures = asyncio.run(label_batch([cluster_a, cluster_b], client))
        assert failures == []
        by_id = {o.cluster_id: o for o in outcomes}
        assert by_id["cluster_00"].label == "App freezes"
        assert by_id["cluster_01"].label == "Paywall cost"

    def test_mix_of_labeled_and_fallback(self) -> None:
        cluster_a = _cluster(cluster_id="cluster_00", quotes=["good quote"])
        cluster_b = _cluster(cluster_id="cluster_01", quotes=["bad quote"])
        client = FakeClient(
            scripted={
                "good quote": _label_json("Good label"),
                "bad quote": "no json here",  # triggers fallback
            }
        )
        outcomes, failures = asyncio.run(label_batch([cluster_a, cluster_b], client))
        assert failures == []
        by_id = {o.cluster_id: o for o in outcomes}
        assert by_id["cluster_00"].status == "labeled"
        assert by_id["cluster_01"].status == "fallback"

    def test_transport_failures_captured(self) -> None:
        cluster_a = _cluster(cluster_id="cluster_00", quotes=["ok"])
        cluster_b = _cluster(cluster_id="cluster_01", quotes=["boom"])
        client = FakeClient(
            scripted={"ok": _label_json("Fine")},
            raise_on={"boom": RuntimeError("transport err")},
        )
        outcomes, failures = asyncio.run(label_batch([cluster_a, cluster_b], client))
        # Transport failures are NOT silent fallbacks — they surface
        # as a separate list so the caller can decide to abort.
        assert len(outcomes) == 1
        assert outcomes[0].cluster_id == "cluster_00"
        assert len(failures) == 1
        assert failures[0][0] == "cluster_01"
        assert isinstance(failures[0][1], RuntimeError)


# =============================================================================
# merge_outcomes — preserves every cluster, rewrites labels
# =============================================================================


class TestMergeOutcomes:
    def test_happy_path_rewrites_labels(self) -> None:
        clusters = [
            _cluster(cluster_id="cluster_00"),
            _cluster(cluster_id="cluster_01", quotes=["x", "y"]),
        ]
        outcomes = [
            LabelOutcome("cluster_00", "Label A", "labeled"),
            LabelOutcome("cluster_01", "Label B", "labeled"),
        ]
        merged = merge_outcomes(clusters, outcomes)
        assert len(merged) == 2
        assert merged[0].label == "Label A"
        assert merged[1].label == "Label B"

    def test_carries_through_other_fields(self) -> None:
        # The L3 → L3b invariant is that every field except ``label``
        # round-trips verbatim. This is what makes L3b a labelling
        # layer and not a free-form rewrite of the cluster artifact.
        original = _cluster(
            cluster_id="cluster_02",
            members=["r1", "r2", "r3"],
            centroid_ref="l3_centroids_custom.npy#2",
            quotes=["a", "b", "c"],
        )
        outcomes = [LabelOutcome("cluster_02", "New label", "labeled")]
        merged = merge_outcomes([original], outcomes)
        assert len(merged) == 1
        got = merged[0]
        assert got.cluster_id == original.cluster_id
        assert got.member_review_ids == original.member_review_ids
        assert got.centroid_vector_ref == original.centroid_vector_ref
        assert got.representative_quotes == original.representative_quotes
        assert got.label == "New label"

    def test_fallback_keeps_placeholder(self) -> None:
        clusters = [_cluster(cluster_id="cluster_05")]
        outcomes = [
            LabelOutcome(
                "cluster_05", "UNLABELED:cluster_05", "fallback", reason="parse err"
            )
        ]
        merged = merge_outcomes(clusters, outcomes)
        assert merged[0].label == "UNLABELED:cluster_05"

    def test_missing_outcome_preserves_cluster(self) -> None:
        # Total function of input: transport failures drop an outcome
        # row, but the cluster still appears in the output with its
        # original placeholder label.
        clusters = [_cluster(cluster_id="cluster_09")]
        merged = merge_outcomes(clusters, outcomes=[])
        assert len(merged) == 1
        assert merged[0].label == "UNLABELED:cluster_09"

    def test_sorted_by_cluster_id(self) -> None:
        # Deterministic order so byte-identical artifacts are
        # reproducible under re-runs (ADR-011).
        clusters = [
            _cluster(cluster_id="cluster_02"),
            _cluster(cluster_id="cluster_00"),
            _cluster(cluster_id="cluster_01"),
        ]
        outcomes = [
            LabelOutcome("cluster_00", "A", "labeled"),
            LabelOutcome("cluster_01", "B", "labeled"),
            LabelOutcome("cluster_02", "C", "labeled"),
        ]
        merged = merge_outcomes(clusters, outcomes)
        assert [c.cluster_id for c in merged] == [
            "cluster_00",
            "cluster_01",
            "cluster_02",
        ]


# =============================================================================
# build_provenance — summary payload
# =============================================================================


class TestBuildProvenance:
    def test_counts(self) -> None:
        outcomes = [
            LabelOutcome("cluster_00", "Voice recognition", "labeled"),
            LabelOutcome("cluster_01", MIXED_LABEL, "labeled"),
            LabelOutcome("cluster_02", "UNLABELED:cluster_02", "fallback", reason="bad"),
        ]
        failures = [("cluster_03", RuntimeError("x"))]
        prov = build_provenance(outcomes, failures, model="model-x")
        assert prov["skill_id"] == SKILL_ID
        assert prov["model"] == "model-x"
        assert prov["cluster_count"] == 4
        assert prov["labeled_count"] == 2
        assert prov["mixed_complaints_count"] == 1
        assert prov["fallback_count"] == 1
        assert prov["transport_failure_count"] == 1

    def test_fallback_reasons_shape(self) -> None:
        outcomes = [
            LabelOutcome("cluster_05", "UNLABELED:cluster_05", "fallback", reason="r1"),
            LabelOutcome("cluster_02", "UNLABELED:cluster_02", "fallback", reason="r2"),
        ]
        prov = build_provenance(outcomes, [], model="m")
        # Sorted by cluster_id for deterministic diffs across runs.
        assert [r["cluster_id"] for r in prov["fallback_reasons"]] == [
            "cluster_02",
            "cluster_05",
        ]
        assert prov["fallback_reasons"][0] == {"cluster_id": "cluster_02", "reason": "r2"}

    def test_transport_failures_shape(self) -> None:
        failures = [
            ("cluster_10", ValueError("nope")),
            ("cluster_03", RuntimeError("boom")),
        ]
        prov = build_provenance([], failures, model="m")
        assert [r["cluster_id"] for r in prov["transport_failures"]] == [
            "cluster_03",
            "cluster_10",
        ]
        assert "RuntimeError: boom" in prov["transport_failures"][0]["error"]


# =============================================================================
# CLI — end-to-end
# =============================================================================


class _FakeClientFactory:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_instance: FakeClient | None = None

    def __call__(self, **kwargs: Any) -> FakeClient:
        self.last_instance = FakeClient(default_response=self.response)
        return self.last_instance


class TestMainCLI:
    def _setup_repo(self, tmp_path: Path) -> tuple[Path, Path, Path, Path]:
        repo = tmp_path / "repo"
        (repo / "data" / "derived").mkdir(parents=True)
        (repo / "data" / "cache").mkdir(parents=True)
        (repo / "pyproject.toml").write_text("[project]\nname='x'\n")
        clusters_path = repo / "data" / "derived" / "l3_clusters.jsonl"
        labeled_path = repo / "data" / "derived" / "l3b_labeled_clusters.jsonl"
        replay_log = repo / "data" / "cache" / "responses.jsonl"
        return repo, clusters_path, labeled_path, replay_log

    def _pin_repo_root(self, monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
        monkeypatch.setattr(l3b_label, "_resolve_repo_root", lambda: repo)

    def _write_clusters(self, path: Path, clusters: list[InsightCluster]) -> None:
        _write_jsonl(path, [c.model_dump(mode="json") for c in clusters])

    def test_end_to_end_writes_labeled_and_meta(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo, clusters_path, labeled_path, replay_log = self._setup_repo(tmp_path)
        clusters = [
            _cluster(cluster_id="cluster_00", quotes=["freezing", "freezes"]),
            _cluster(cluster_id="cluster_01", quotes=["expensive", "paywall"]),
        ]
        self._write_clusters(clusters_path, clusters)
        self._pin_repo_root(monkeypatch, repo)

        factory = _FakeClientFactory(_label_json("Stub label"))
        monkeypatch.setattr(l3b_label, "Client", factory)

        rc = main(
            [
                "--clusters",
                str(clusters_path),
                "--output",
                str(labeled_path),
                "--replay-log",
                str(replay_log),
                "--run-id",
                "test-run",
                "--mode",
                "live",
            ]
        )
        assert rc == 0
        assert labeled_path.exists()

        labeled_lines = [
            json.loads(x)
            for x in labeled_path.read_text().splitlines()
            if x.strip()
        ]
        assert len(labeled_lines) == 2
        assert all(row["label"] == "Stub label" for row in labeled_lines)
        # Every non-label field survives intact.
        assert {row["cluster_id"] for row in labeled_lines} == {
            "cluster_00",
            "cluster_01",
        }

        # .meta.json sidecar with skill_hashes populated — this is the
        # concrete audit boundary that distinguishes L3b from L3.
        meta_path = labeled_path.with_suffix(labeled_path.suffix + ".meta.json")
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["run_id"] == "test-run"
        assert meta["layer"] == LAYER_NAME
        assert SKILL_ID in meta["skill_hashes"]
        assert meta["skill_hashes"][SKILL_ID] == skill_hash()
        # input_hashes covers the source cluster file — mechanically
        # links labelled output back to the exact L3 artifact it was
        # derived from.
        assert clusters_path.name in meta["input_hashes"]

        # .provenance.json sidecar — auditor-facing.
        prov_path = labeled_path.with_suffix(".provenance.json")
        assert prov_path.exists()
        prov = json.loads(prov_path.read_text())
        assert prov["cluster_count"] == 2
        assert prov["labeled_count"] == 2
        assert prov["fallback_count"] == 0

    def test_end_to_end_records_fallback_in_provenance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo, clusters_path, labeled_path, replay_log = self._setup_repo(tmp_path)
        clusters = [_cluster(cluster_id="cluster_00", quotes=["x"])]
        self._write_clusters(clusters_path, clusters)
        self._pin_repo_root(monkeypatch, repo)

        # Bad response → parse fails → fallback. Exit code 0 (fallback
        # is a traceable signal, not an error).
        factory = _FakeClientFactory("no json at all")
        monkeypatch.setattr(l3b_label, "Client", factory)

        rc = main(
            [
                "--clusters",
                str(clusters_path),
                "--output",
                str(labeled_path),
                "--replay-log",
                str(replay_log),
                "--run-id",
                "test-run-fb",
                "--mode",
                "live",
            ]
        )
        assert rc == 0

        labeled_lines = [
            json.loads(x)
            for x in labeled_path.read_text().splitlines()
            if x.strip()
        ]
        assert len(labeled_lines) == 1
        # Placeholder survives the fallback — the artifact is
        # scannable-by-eye for fallbacks.
        assert labeled_lines[0]["label"] == "UNLABELED:cluster_00"

        prov = json.loads(
            labeled_path.with_suffix(".provenance.json").read_text()
        )
        assert prov["fallback_count"] == 1
        assert prov["labeled_count"] == 0
        assert prov["fallback_reasons"][0]["cluster_id"] == "cluster_00"

    def test_empty_input_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo, clusters_path, labeled_path, replay_log = self._setup_repo(tmp_path)
        # An empty clusters file is a misconfiguration (pointed at the
        # wrong file / L3 hasn't run / read_jsonl glob miss). Fail loud.
        clusters_path.write_text("")
        self._pin_repo_root(monkeypatch, repo)

        rc = main(
            [
                "--clusters",
                str(clusters_path),
                "--output",
                str(labeled_path),
                "--replay-log",
                str(replay_log),
                "--run-id",
                "test-empty",
                "--mode",
                "live",
            ]
        )
        assert rc == 1
        # Output file not created — we bailed before the write.
        assert not labeled_path.exists()


# =============================================================================
# load_clusters — ingest validation
# =============================================================================


class TestLoadClusters:
    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "in.jsonl"
        cluster = _cluster(cluster_id="cluster_42")
        _write_jsonl(path, [cluster.model_dump(mode="json")])
        loaded = load_clusters(path)
        assert len(loaded) == 1
        assert loaded[0].cluster_id == "cluster_42"

    def test_invalid_row_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "in.jsonl"
        # Missing required field — Pydantic must reject it and the
        # loader must not silently drop the row. Silent drops would
        # desync the cluster count vs the L3 output.
        path.write_text('{"cluster_id": "cluster_00"}\n')
        with pytest.raises(ValueError):
            load_clusters(path)
