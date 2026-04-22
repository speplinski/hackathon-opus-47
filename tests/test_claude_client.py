"""Tests for the dual-mode Claude client (ADR-011, ADR-015)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
import pytest

from auditable_design.claude_client import (
    PRICING_USD_PER_MTOK,
    ClaudeResponse,
    Client,
    CostCeilingExceeded,
    ReplayLogCorrupt,
    ReplayMiss,
    estimate_cost_usd,
)

# A skill_hash shape matches the schema constraint (64-char sha256 hex).
_SKILL_HASH = "a" * 64
_MODEL_LIVE = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Fake SDK
# ---------------------------------------------------------------------------


@dataclass
class _FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class _FakeMessage:
    content: list[_FakeTextBlock]
    usage: _FakeUsage


class _FakeMessages:
    """Test double that records calls and returns canned responses.

    Pass ``raises`` to simulate transient SDK errors; each exception is
    popped in order, then the canned response is returned.
    """

    def __init__(
        self,
        *,
        response_text: str = "OK",
        input_tokens: int = 100,
        output_tokens: int = 50,
        raises: list[Exception] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response_text = response_text
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._raises = list(raises or [])

    async def create(self, **kwargs: Any) -> _FakeMessage:
        self.calls.append(kwargs)
        if self._raises:
            raise self._raises.pop(0)
        return _FakeMessage(
            content=[_FakeTextBlock(text=self._response_text)],
            usage=_FakeUsage(
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
            ),
        )


class _FakeSDK:
    def __init__(self, messages: _FakeMessages) -> None:
        self.messages = messages


def _make_fake_api_error(cls: type[Exception]) -> Exception:
    """Build an anthropic SDK error without a real HTTP response.

    The SDK's error classes accept ``message`` / ``response`` kwargs
    that we don't need here. Subclassing sidesteps the real ctor.
    """

    class _E(cls):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            super().__init__("fake")  # type: ignore[call-arg]

    # Some SDK errors override __init__ in ways that reject args; if
    # that happens, fall back to Exception.__init__.
    try:
        return _E()
    except TypeError:
        inst = cls.__new__(cls)
        Exception.__init__(inst, "fake")
        return inst


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


def test_pricing_table_covers_core_models() -> None:
    """The four models the plan actually uses must be priced."""
    for m in ("claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"):
        assert m in PRICING_USD_PER_MTOK


def test_estimate_cost_is_linear() -> None:
    model = "claude-sonnet-4-6"
    per_in, per_out = PRICING_USD_PER_MTOK[model]
    assert estimate_cost_usd(model, 1_000_000, 0) == pytest.approx(per_in)
    assert estimate_cost_usd(model, 0, 1_000_000) == pytest.approx(per_out)
    assert estimate_cost_usd(model, 0, 0) == 0.0


def test_estimate_cost_rejects_unknown_model() -> None:
    with pytest.raises(KeyError):
        estimate_cost_usd("claude-future-9", 100, 100)


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def test_rejects_bad_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mode must be"):
        Client(mode="hybrid", run_id="r", replay_log_path=tmp_path / "r.jsonl")  # type: ignore[arg-type]


def test_rejects_bad_concurrency(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="concurrency"):
        Client(
            mode="live",
            run_id="r",
            replay_log_path=tmp_path / "r.jsonl",
            concurrency=0,
        )


def test_rejects_bad_usd_ceiling(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="usd_ceiling"):
        Client(
            mode="live",
            run_id="r",
            replay_log_path=tmp_path / "r.jsonl",
            usd_ceiling=-0.01,
        )


# ---------------------------------------------------------------------------
# Replay-log parsing
# ---------------------------------------------------------------------------


def test_missing_log_is_empty_cache(tmp_path: Path) -> None:
    c = Client(mode="live", run_id="r", replay_log_path=tmp_path / "missing.jsonl",
               sdk_client=_FakeSDK(_FakeMessages()))
    assert c.cache_size == 0


def test_corrupt_log_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(ReplayLogCorrupt):
        Client(mode="replay", run_id="r", replay_log_path=path)


def test_duplicate_key_hash_rejected(tmp_path: Path) -> None:
    path = tmp_path / "dup.jsonl"
    entry = {"call_id": "c", "key_hash": "x" * 64, "skill_id": "s", "skill_hash": _SKILL_HASH,
             "model": "m", "temperature": 0.0, "prompt": "p", "response": "r",
             "input_tokens": 1, "output_tokens": 2, "timestamp": "2026-04-22T00:00:00+00:00"}
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
        f.write(json.dumps(entry) + "\n")
    with pytest.raises(ReplayLogCorrupt, match="duplicate"):
        Client(mode="replay", run_id="r", replay_log_path=path)


# ---------------------------------------------------------------------------
# Live mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_call_hits_sdk_and_appends_log(tmp_path: Path) -> None:
    sdk = _FakeSDK(_FakeMessages(response_text="verdict-tail", input_tokens=100, output_tokens=40))
    log = tmp_path / "cache" / "responses.jsonl"
    c = Client(mode="live", run_id="test-run", replay_log_path=log, sdk_client=sdk)

    resp = await c.call(
        system="you are an auditor",
        user="audit this",
        model=_MODEL_LIVE,
        skill_id="audit-usability-fundamentals",
        skill_hash=_SKILL_HASH,
        temperature=0.0,
    )

    assert isinstance(resp, ClaudeResponse)
    assert resp.cache_hit is False
    assert resp.response == "verdict-tail"
    assert resp.input_tokens == 100
    assert resp.output_tokens == 40
    assert resp.cost_usd > 0
    assert resp.call_id.startswith(resp.key_hash[:12])

    # SDK was called with the expected kwargs.
    assert len(sdk.messages.calls) == 1
    kwargs = sdk.messages.calls[0]
    assert kwargs["model"] == _MODEL_LIVE
    assert kwargs["system"] == "you are an auditor"
    assert kwargs["messages"] == [{"role": "user", "content": "audit this"}]

    # Log file exists and contains one JSONL record.
    assert log.exists()
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["skill_id"] == "audit-usability-fundamentals"
    assert entry["key_hash"] == resp.key_hash
    assert entry["cost_usd"] == pytest.approx(resp.cost_usd)

    # cumulative_usd was advanced.
    assert c.cumulative_usd == pytest.approx(resp.cost_usd)


@pytest.mark.asyncio
async def test_live_second_identical_call_is_cache_hit(tmp_path: Path) -> None:
    sdk = _FakeSDK(_FakeMessages())
    c = Client(mode="live", run_id="r", replay_log_path=tmp_path / "r.jsonl", sdk_client=sdk)
    kwargs = dict(system="sys", user="usr", model=_MODEL_LIVE,
                  skill_id="s", skill_hash=_SKILL_HASH, temperature=0.0)

    first = await c.call(**kwargs)
    second = await c.call(**kwargs)

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.call_id == first.call_id
    # SDK only called once.
    assert len(sdk.messages.calls) == 1
    # Cost only charged once.
    assert c.cumulative_usd == pytest.approx(first.cost_usd)


@pytest.mark.asyncio
async def test_live_different_prompts_produce_different_keys(tmp_path: Path) -> None:
    sdk = _FakeSDK(_FakeMessages())
    c = Client(mode="live", run_id="r", replay_log_path=tmp_path / "r.jsonl", sdk_client=sdk)
    base = dict(system="sys", model=_MODEL_LIVE, skill_id="s", skill_hash=_SKILL_HASH, temperature=0.0)
    a = await c.call(user="alpha", **base)
    b = await c.call(user="beta", **base)
    assert a.key_hash != b.key_hash
    assert len(sdk.messages.calls) == 2


@pytest.mark.asyncio
async def test_elapsed_s_populated_on_live_call(tmp_path: Path) -> None:
    """§10.1 wants per-call elapsed_s in the observability log. The client
    measures it around the dispatch and exposes it on ClaudeResponse so
    the orchestrator can copy it without re-timing.
    """

    class _SlowMessages(_FakeMessages):
        async def create(self, **kwargs: Any) -> _FakeMessage:  # type: ignore[override]
            await asyncio.sleep(0.02)
            return await super().create(**kwargs)

    sdk = _FakeSDK(_SlowMessages())
    c = Client(mode="live", run_id="r", replay_log_path=tmp_path / "r.jsonl", sdk_client=sdk)
    resp = await c.call(
        system="s", user="u", model=_MODEL_LIVE,
        skill_id="s", skill_hash=_SKILL_HASH, temperature=0.0,
    )
    # Lower bound: we slept 20 ms inside the fake. Upper bound loose to
    # tolerate loaded CI machines.
    assert resp.elapsed_s >= 0.015, f"expected elapsed_s ≥ ~15ms, got {resp.elapsed_s}"
    assert resp.elapsed_s < 2.0, f"elapsed_s suspiciously high: {resp.elapsed_s}"


@pytest.mark.asyncio
async def test_elapsed_s_zero_on_cache_hit(tmp_path: Path) -> None:
    """Cache hits and replay reads must not carry live-run latency —
    that number belongs to the run that produced the entry, not this one.
    """
    sdk = _FakeSDK(_FakeMessages())
    c = Client(mode="live", run_id="r", replay_log_path=tmp_path / "r.jsonl", sdk_client=sdk)
    kwargs = dict(system="s", user="u", model=_MODEL_LIVE,
                  skill_id="s", skill_hash=_SKILL_HASH, temperature=0.0)
    first = await c.call(**kwargs)
    second = await c.call(**kwargs)
    assert first.cache_hit is False
    assert first.elapsed_s > 0.0
    assert second.cache_hit is True
    assert second.elapsed_s == 0.0


@pytest.mark.asyncio
async def test_max_tokens_affects_key_hash(tmp_path: Path) -> None:
    """Two calls that differ only in ``max_tokens`` must produce
    different key_hashes — otherwise a truncated reply could poison the
    replay log entry used by a later full-budget call.
    """
    sdk = _FakeSDK(_FakeMessages())
    c = Client(mode="live", run_id="r", replay_log_path=tmp_path / "r.jsonl", sdk_client=sdk)
    base = dict(system="sys", user="u", model=_MODEL_LIVE,
                skill_id="s", skill_hash=_SKILL_HASH, temperature=0.0)
    a = await c.call(max_tokens=512, **base)
    b = await c.call(max_tokens=4096, **base)
    assert a.key_hash != b.key_hash
    # Both calls dispatched (no cache collision).
    assert len(sdk.messages.calls) == 2


@pytest.mark.asyncio
async def test_live_rejects_short_skill_hash(tmp_path: Path) -> None:
    c = Client(mode="live", run_id="r", replay_log_path=tmp_path / "r.jsonl",
               sdk_client=_FakeSDK(_FakeMessages()))
    with pytest.raises(ValueError, match="skill_hash"):
        await c.call(
            system="s", user="u", model=_MODEL_LIVE,
            skill_id="x", skill_hash="short", temperature=0.0,
        )


# ---------------------------------------------------------------------------
# Sampling-param omission for Opus 4.7+
# ---------------------------------------------------------------------------


def test_omits_sampling_params_matches_opus_4_7_family() -> None:
    """Helper predicate: only Opus 4.7-family models drop temperature.

    Extend when a second model adopts the same restriction — the
    assertions below will remind us to update both the helper and
    this test in the same PR.
    """
    from auditable_design.claude_client import _omits_sampling_params

    # 4.7 and dated variants — drop temperature.
    assert _omits_sampling_params("claude-opus-4-7")
    assert _omits_sampling_params("claude-opus-4-7-20260416")
    # Everything else we currently dispatch keeps temperature.
    assert not _omits_sampling_params("claude-opus-4-6")
    assert not _omits_sampling_params("claude-sonnet-4-6")
    assert not _omits_sampling_params("claude-haiku-4-5-20251001")


@pytest.mark.asyncio
async def test_opus_4_7_dispatch_omits_temperature(tmp_path: Path) -> None:
    """Opus 4.7 returns 400 on any non-default ``temperature``. The
    client must not send it. We still log caller-intent
    (``temperature=0.0``) to the replay entry — that is the audit
    record; the API payload is a downstream representation (ADR-011).
    """
    sdk = _FakeSDK(_FakeMessages())
    log = tmp_path / "r.jsonl"
    c = Client(mode="live", run_id="r", replay_log_path=log, sdk_client=sdk)
    resp = await c.call(
        system="s", user="u", model="claude-opus-4-7",
        skill_id="x", skill_hash=_SKILL_HASH, temperature=0.0,
    )
    kwargs = sdk.messages.calls[0]
    assert "temperature" not in kwargs, (
        f"temperature must not be sent to Opus 4.7 — API would 400. "
        f"Got kwargs keys: {sorted(kwargs)}"
    )
    # But the replay log records caller-intent temperature.
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert entry["temperature"] == 0.0
    # And ClaudeResponse still carries it for downstream observability.
    assert resp.temperature == 0.0


@pytest.mark.asyncio
async def test_opus_4_6_dispatch_still_sends_temperature(tmp_path: Path) -> None:
    """Belt-and-braces: the 4.7 carve-out must not leak to 4.6 — older
    models still require temperature to be sent.
    """
    sdk = _FakeSDK(_FakeMessages())
    c = Client(mode="live", run_id="r", replay_log_path=tmp_path / "r.jsonl", sdk_client=sdk)
    await c.call(
        system="s", user="u", model="claude-opus-4-6",
        skill_id="x", skill_hash=_SKILL_HASH, temperature=0.0,
    )
    kwargs = sdk.messages.calls[0]
    assert kwargs.get("temperature") == 0.0


# ---------------------------------------------------------------------------
# Replay mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_mode_serves_existing_entry(tmp_path: Path) -> None:
    # Phase 1: produce an entry in live mode.
    sdk = _FakeSDK(_FakeMessages(response_text="frozen"))
    log = tmp_path / "responses.jsonl"
    live = Client(mode="live", run_id="r-live", replay_log_path=log, sdk_client=sdk)
    args = dict(system="s", user="u", model=_MODEL_LIVE,
                skill_id="skill-one", skill_hash=_SKILL_HASH, temperature=0.0)
    original = await live.call(**args)

    # Phase 2: reviewer replays the exact same call — no SDK, no key.
    replay = Client(mode="replay", run_id="r-replay", replay_log_path=log)
    served = await replay.call(**args)

    assert served.cache_hit is True
    assert served.response == "frozen"
    assert served.call_id == original.call_id


@pytest.mark.asyncio
async def test_replay_miss_raises(tmp_path: Path) -> None:
    replay = Client(mode="replay", run_id="r", replay_log_path=tmp_path / "empty.jsonl")
    with pytest.raises(ReplayMiss):
        await replay.call(
            system="s", user="u", model=_MODEL_LIVE,
            skill_id="x", skill_hash=_SKILL_HASH, temperature=0.0,
        )


def test_replay_mode_refuses_sdk_client(tmp_path: Path) -> None:
    """Defensive: replay mode must refuse to even hold an SDK handle.

    The failure mode we block is a misconfigured client that silently
    falls through to the API on a cache miss. The constructor catches
    it before any call is made, rather than waiting for a runtime miss.
    """
    sdk = _FakeSDK(_FakeMessages())
    with pytest.raises(ValueError, match="replay mode refuses"):
        Client(mode="replay", run_id="r", replay_log_path=tmp_path / "e.jsonl", sdk_client=sdk)
    # And the SDK was never touched.
    assert sdk.messages.calls == []


# ---------------------------------------------------------------------------
# Cost kill-switch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_ceiling_blocks_new_calls(tmp_path: Path) -> None:
    # Each Opus call at 100-in/50-out costs 100*15/1M + 50*75/1M = 0.00525 USD.
    # Ceiling 0.005 → first call succeeds; after it cumulative (0.00525) ≥
    # 0.005, so the *next* call trips the kill-switch before dispatch.
    sdk = _FakeSDK(_FakeMessages(input_tokens=100, output_tokens=50))
    c = Client(
        mode="live", run_id="r",
        replay_log_path=tmp_path / "r.jsonl",
        sdk_client=sdk,
        usd_ceiling=0.005,
    )
    await c.call(system="s", user="u1", model="claude-opus-4-7",
                 skill_id="x", skill_hash=_SKILL_HASH, temperature=0.0)
    assert c.cumulative_usd >= 0.005
    # Second distinct call should trip the ceiling.
    with pytest.raises(CostCeilingExceeded):
        await c.call(system="s", user="u2", model="claude-opus-4-7",
                     skill_id="x", skill_hash=_SKILL_HASH, temperature=0.0)
    # And the blocked call should not have been dispatched.
    assert len(sdk.messages.calls) == 1


@pytest.mark.asyncio
async def test_replay_bypasses_ceiling(tmp_path: Path) -> None:
    # Seed a log entry.
    log = tmp_path / "r.jsonl"
    entry = {
        "call_id": "c-0000", "key_hash": "z" * 64, "skill_id": "x",
        "skill_hash": _SKILL_HASH, "model": _MODEL_LIVE, "temperature": 0.0,
        "prompt": "SYSTEM:\tsys\tUSER:\tu", "response": "cached",
        "input_tokens": 1, "output_tokens": 2, "cost_usd": 0.0001,
        "timestamp": "2026-04-22T00:00:00+00:00",
    }
    # We deliberately don't match the canonical key_hash; force by
    # constructing via the real hash function instead.
    from auditable_design.claude_client import _key_hash

    kh = _key_hash(
        skill_id="x", skill_hash=_SKILL_HASH, model=_MODEL_LIVE,
        temperature=0.0, max_tokens=2048, system="sys", user="u",
    )
    entry["key_hash"] = kh
    log.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    c = Client(mode="replay", run_id="r", replay_log_path=log, usd_ceiling=0.0)
    # Ceiling is 0 but replay mode doesn't check it.
    r = await c.call(system="sys", user="u", model=_MODEL_LIVE,
                     skill_id="x", skill_hash=_SKILL_HASH, temperature=0.0)
    assert r.cache_hit is True
    assert r.response == "cached"


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_on_rate_limit_then_success(tmp_path: Path) -> None:
    err = _make_fake_api_error(anthropic.RateLimitError)
    sdk = _FakeSDK(_FakeMessages(raises=[err]))
    c = Client(
        mode="live", run_id="r",
        replay_log_path=tmp_path / "r.jsonl",
        sdk_client=sdk,
        retry_attempts=3,
    )
    # Avoid sleeping between attempts: patch tenacity's wait via a
    # near-zero initial. Our actual wait is exponential-jitter; for the
    # test we accept the first-attempt wait of ~1 s. That is fine —
    # 1 retry × ~1 s is the same order as a normal test round-trip.
    # (If this ever slows the suite, inject a stub wait strategy.)
    resp = await c.call(
        system="s", user="u", model=_MODEL_LIVE,
        skill_id="x", skill_hash=_SKILL_HASH, temperature=0.0,
    )
    assert resp.response == "OK"
    # Two calls: the failing one, then the success.
    assert len(sdk.messages.calls) == 2


@pytest.mark.asyncio
async def test_no_retry_on_client_error(tmp_path: Path) -> None:
    # BadRequestError is not in _RETRIABLE_ERRORS — must propagate.
    err = _make_fake_api_error(anthropic.BadRequestError)
    sdk = _FakeSDK(_FakeMessages(raises=[err]))
    c = Client(
        mode="live", run_id="r",
        replay_log_path=tmp_path / "r.jsonl",
        sdk_client=sdk,
        retry_attempts=4,
    )
    with pytest.raises(anthropic.BadRequestError):
        await c.call(
            system="s", user="u", model=_MODEL_LIVE,
            skill_id="x", skill_hash=_SKILL_HASH, temperature=0.0,
        )
    assert len(sdk.messages.calls) == 1  # no retry


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semaphore_limits_concurrency(tmp_path: Path) -> None:
    """Verify the semaphore caps in-flight calls at the configured level.

    We make the fake SDK block on an event and count how many calls
    enter flight before we release it.
    """
    in_flight = 0
    peak = 0
    gate = asyncio.Event()

    class _BlockingMessages(_FakeMessages):
        async def create(self, **kwargs: Any) -> _FakeMessage:  # type: ignore[override]
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                await gate.wait()
                return await super().create(**kwargs)
            finally:
                in_flight -= 1

    sdk = _FakeSDK(_BlockingMessages())
    c = Client(
        mode="live", run_id="r",
        replay_log_path=tmp_path / "r.jsonl",
        sdk_client=sdk,
        concurrency=2,
    )

    async def one(i: int) -> ClaudeResponse:
        return await c.call(
            system="s", user=f"u-{i}", model=_MODEL_LIVE,
            skill_id="x", skill_hash=_SKILL_HASH, temperature=0.0,
        )

    tasks = [asyncio.create_task(one(i)) for i in range(5)]
    # Let a couple of coroutines reach the gate.
    await asyncio.sleep(0.05)
    assert peak <= 2, f"expected peak ≤ 2, got {peak}"
    gate.set()
    await asyncio.gather(*tasks)
    # Peak should have stayed at 2 across the whole run.
    assert peak == 2
