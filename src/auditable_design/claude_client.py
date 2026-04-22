"""Dual-mode Claude client with replay log and cost kill-switch.

Two modes, exactly (ADR-011, ARCHITECTURE.md §11.2):

``live``
    Calls the Anthropic Messages API. Every call is appended to the
    replay log (``data/cache/responses.jsonl``) and the per-run USD
    counter is advanced. Used only on the author's machine.

``replay``
    Resolves every call from the replay log only. A cache miss raises
    :class:`ReplayMiss`; there is no silent fallback to the API. Used
    by reviewers and by CI — reproducibility without an API key.

Cost kill-switch (ADR-015, ARCHITECTURE.md §5.5)
    Live mode tracks cumulative USD under an :class:`asyncio.Lock` and
    refuses *new* calls once :attr:`Client.cumulative_usd` reaches the
    configured ceiling. In-flight coroutines complete; what existed is
    preserved. Replay mode bypasses the ceiling entirely (cost = 0).

Scope — deliberately small
    This is the Day-2 minimal client. It does ONE thing per call:
    attach system + user text, dispatch, append to the log. Things that
    are *not* in here yet, because no call site needs them yet:

    - Request coalescing (ADR-005). Re-add when a concrete caller
      shows measurable duplicate-in-flight pressure.
    - Structured-output schema enforcement. Layers parse and Pydantic-
      validate their own outputs; the client returns raw text.
    - Quarantine dir for invalid JSON. Same reason — the layer owns
      its own validity contract.
    - Token-bucket rate limiter. Anthropic's 429 + our
      :mod:`tenacity` backoff is sufficient at this scale.
    - Per-call prompt+``max_tokens`` ceiling (ARCHITECTURE.md §5.5
      pkt 1). The per-run USD kill-switch is enough at pilot scale;
      the per-call guard wants `skill_config.yaml` which is Day 3+.

    Adding any of the above without a pointable call-site need is the
    kind of complexity this file is structured to resist.

See also
    ARCHITECTURE.md §5.2 (client responsibilities narrative), §10
    (reproducibility & evidence), §11.2 (client modes).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import anthropic
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "PRICING_USD_PER_MTOK",
    "ClaudeResponse",
    "Client",
    "CostCeilingExceeded",
    "ReplayLogCorrupt",
    "ReplayMiss",
    "estimate_cost_usd",
]

_log = logging.getLogger(__name__)

ClientMode = Literal["live", "replay"]


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
#
# USD per 1M tokens, (input, output). Extend as new models come online.
# Kept inline because `claude_client` is currently the only consumer.
# Extract into `pricing.py` the moment a second module imports from here.
# Prices below are placeholders set to the order-of-magnitude known for
# the Claude 4 family at time of writing — readers should treat the
# kill-switch numbers as approximate until we verify against billing.
#
# Unknown model → raises in `estimate_cost_usd`. We would rather fail
# loudly than silently charge the wrong amount to the wrong counter.
PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    # Opus 4.x family
    "claude-opus-4-6": (15.0, 75.0),
    "claude-opus-4-7": (15.0, 75.0),
    # Sonnet 4.x family
    "claude-sonnet-4-6": (3.0, 15.0),
    # Haiku 4.x family
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for a single Claude call.

    Cache tokens (``cache_creation_input_tokens`` and
    ``cache_read_input_tokens``) are callers' responsibility — if the
    caller wants them counted, they should add them into ``input_tokens``
    before calling. Keeping the signature narrow means the cost model
    stays one-line-auditable.
    """
    if model not in PRICING_USD_PER_MTOK:
        raise KeyError(
            f"no pricing configured for model {model!r}; extend PRICING_USD_PER_MTOK"
        )
    per_in, per_out = PRICING_USD_PER_MTOK[model]
    return (input_tokens / 1_000_000.0) * per_in + (output_tokens / 1_000_000.0) * per_out


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ReplayMiss(RuntimeError):
    """Raised in replay mode when the requested call is not in the log.

    Carrying the ``key_hash`` and ``skill_id`` lets the caller decide
    whether to re-run the pipeline in live mode to refill the gap, or
    whether the miss indicates a more serious divergence (e.g. a
    skill-content change that was not committed together with the log).
    """


class ReplayLogCorrupt(RuntimeError):
    """Raised when the replay log contains an unparseable line or a
    duplicate ``key_hash``.

    We treat duplicates as corruption rather than silently picking one
    — the manifest (``scripts/generate_replay_manifest.py``) enumerates
    entries in order, so a dup would produce non-deterministic replay.
    """


class CostCeilingExceeded(RuntimeError):
    """Raised by :meth:`Client.call` when the per-run USD ceiling has
    been crossed. In-flight coroutines complete; no new calls dispatch.

    The ceiling is **soft** by up to ``concurrency × max-per-call-cost``:
    all in-flight calls under the semaphore are allowed to finish and
    charge, even if their combined completion pushes the cumulative
    past the ceiling. The hard guarantee is only that no *new* call
    will dispatch after the ceiling has been crossed. Set
    ``usd_ceiling`` with some headroom (ADR-015, ARCHITECTURE.md §5.5).
    """


# ---------------------------------------------------------------------------
# Response record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClaudeResponse:
    """Return value of :meth:`Client.call`.

    Identical shape in both modes — downstream code cannot tell from
    the object alone whether it came from the API or from the log.
    ``cache_hit`` is True iff the call was served from the replay log
    (either because we are in replay mode, or because live mode hit an
    existing entry with the same ``key_hash``).

    ``elapsed_s`` is the wall-clock time the *dispatch* took (just the
    API round-trip + retries; not log I/O). It is ``0.0`` for cache hits
    and for replay mode — the original live-run latency is not stored
    in the replay log because it's a property of that run, not of the
    call. The orchestrator copies ``elapsed_s`` into
    ``data/log/claude_calls.jsonl`` per ARCHITECTURE.md §10.1.
    """

    call_id: str
    key_hash: str
    skill_id: str
    skill_hash: str
    model: str
    temperature: float
    prompt: str
    response: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    timestamp: str
    cache_hit: bool
    elapsed_s: float


# ---------------------------------------------------------------------------
# Key hashing
# ---------------------------------------------------------------------------


def _key_hash(
    *,
    skill_id: str,
    skill_hash: str,
    model: str,
    temperature: float,
    max_tokens: int,
    system: str,
    user: str,
) -> str:
    """Compute the deterministic cache key for a call.

    The canonical form is a tab-separated list of fields followed by
    a ``\\x00``-separated payload — sha256 of that string.

    Why not `sha256(json.dumps(…))`? Because JSON does not sort keys
    by default, and any change in Python's hash seed would produce a
    different digest. Explicit string canonicalisation is both cheaper
    and impossible to regress.

    ``max_tokens`` is part of the key because the same prompt truncated
    at different budgets can yield semantically different outputs (a
    truncated verdict is not the same evidence as a full one). Two calls
    that differ only in ``max_tokens`` must not collide in the replay
    log.
    """
    # Python floats stringify stably for common values; for
    # temperature we use ``repr`` to preserve exact representation.
    canon = "\t".join(
        (
            skill_id,
            skill_hash,
            model,
            repr(float(temperature)),
            str(int(max_tokens)),
        )
    )
    body = "\x00".join((system, user))
    return hashlib.sha256((canon + "\x00" + body).encode("utf-8")).hexdigest()


def _canonical_prompt(system: str, user: str) -> str:
    """Render the system + user pair into a single replay-log field.

    Keeping both parts stitched means the replay log is self-describing
    — a reviewer inspecting ``responses.jsonl`` sees exactly what was
    sent. Tab-delimited boundaries are robust because ``\\n`` occurs
    freely inside both system prompts and user content.
    """
    return f"SYSTEM:\t{system}\tUSER:\t{user}"


# ---------------------------------------------------------------------------
# Replay log I/O
# ---------------------------------------------------------------------------


def _load_replay_log(path: Path) -> dict[str, dict[str, Any]]:
    """Read ``responses.jsonl`` into ``{key_hash: entry}``.

    Missing file → empty dict (expected on first live run).

    Raises:
        ReplayLogCorrupt: on a malformed line or a duplicate key_hash.
    """
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                raise ReplayLogCorrupt(
                    f"{path}: invalid JSON at line {lineno}: {e}"
                ) from e
            kh = entry.get("key_hash")
            if not isinstance(kh, str) or not kh:
                raise ReplayLogCorrupt(
                    f"{path}: line {lineno} missing or invalid key_hash"
                )
            if kh in out:
                raise ReplayLogCorrupt(
                    f"{path}: duplicate key_hash {kh[:16]}… at line {lineno}"
                )
            out[kh] = entry
    return out


def _append_jsonl(path: Path, entry: Mapping[str, Any]) -> None:
    """Append a single record to ``path``. POSIX O_APPEND makes the
    write atomic per line for records smaller than PIPE_BUF (typically
    4 KiB+); our records are well under that. Parent directory is
    assumed to exist — the caller is responsible.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------------------
# SDK response helpers
# ---------------------------------------------------------------------------


def _extract_text(message: Any) -> str:
    """Concatenate every ``TextBlock.text`` in a Messages-API response.

    Opus responses sometimes split output across multiple blocks when
    tools or thinking traces are in play. Concatenating rather than
    taking ``content[0]`` avoids silently dropping content if the SDK
    ever reorders blocks.
    """
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        # Duck-typed — TextBlock exposes ``.text``; tool_use blocks don't.
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


# Exceptions we retry. The full anthropic hierarchy includes client
# errors (400/401/403/404/422) which are not retriable — a malformed
# request will not self-heal. We retry only on transient-looking
# classes.
_RETRIABLE_ERRORS: tuple[type[BaseException], ...] = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class Client:
    """Dual-mode async wrapper around :class:`anthropic.AsyncAnthropic`.

    Args:
        mode:
            ``"live"`` or ``"replay"``. See module docstring.
        run_id:
            The current :class:`RunContext` id — used only for log
            messages; persistence does not depend on it.
        replay_log_path:
            Path to ``responses.jsonl``. Defaults to
            ``data/cache/responses.jsonl`` under the current working
            directory.
        usd_ceiling:
            Per-run USD kill-switch. Live mode refuses to start a new
            call once cumulative spend ≥ this value. Ignored in replay.
        concurrency:
            Max in-flight API calls in live mode (ADR-005 / P3).
        sdk_client:
            Injected :class:`anthropic.AsyncAnthropic` (or any object
            with the same ``.messages.create(...)`` async method). If
            ``None`` in live mode, one is lazy-constructed on first
            use. Tests pass a fake here.
        retry_attempts:
            Total attempts including the first (default 4 → 3 retries).
    """

    def __init__(
        self,
        *,
        mode: ClientMode,
        run_id: str,
        replay_log_path: str | os.PathLike[str] = "data/cache/responses.jsonl",
        usd_ceiling: float = 15.0,
        concurrency: int = 6,
        sdk_client: Any | None = None,
        retry_attempts: int = 4,
    ) -> None:
        if mode not in ("live", "replay"):
            raise ValueError(f"mode must be 'live' or 'replay', got {mode!r}")
        if concurrency < 1:
            raise ValueError(f"concurrency must be ≥ 1, got {concurrency}")
        if usd_ceiling < 0:
            raise ValueError(f"usd_ceiling must be ≥ 0, got {usd_ceiling}")
        if retry_attempts < 1:
            raise ValueError(f"retry_attempts must be ≥ 1, got {retry_attempts}")
        # Replay mode must never hold an SDK handle — not even a fake one.
        # A misconfigured client that secretly has an API connection is
        # exactly the failure mode replay mode exists to prevent.
        if mode == "replay" and sdk_client is not None:
            raise ValueError(
                "replay mode refuses an injected sdk_client; replay must never "
                "dispatch — drop sdk_client or switch mode to 'live'"
            )

        self._mode: ClientMode = mode
        self._run_id = run_id
        self._log_path = Path(replay_log_path)
        self._usd_ceiling = float(usd_ceiling)
        self._sem = asyncio.Semaphore(concurrency)
        self._cost_lock = asyncio.Lock()
        self._append_lock = asyncio.Lock()
        self._cumulative_usd = 0.0
        self._sdk_client: Any | None = sdk_client
        self._retry_attempts = retry_attempts
        # Pre-load the log. In replay mode this is mandatory; in live
        # mode it lets us serve cache hits for idempotent re-runs
        # without re-spending on identical prompts.
        self._cache: dict[str, dict[str, Any]] = _load_replay_log(self._log_path)

    # -- Read-only accessors -------------------------------------------------

    @property
    def mode(self) -> ClientMode:
        return self._mode

    @property
    def cumulative_usd(self) -> float:
        return self._cumulative_usd

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    # -- Core entry point ----------------------------------------------------

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
        """Resolve one Claude call — from cache, from the API, or raise.

        The branch is:

        1. Compute ``key_hash``.
        2. If a log entry exists for it → return it (``cache_hit=True``).
        3. If in replay mode and no entry → :class:`ReplayMiss`.
        4. In live mode: check the cost ceiling → acquire the semaphore
           → call the API with retries → append to the log → return.
        """
        if not skill_hash or len(skill_hash) != 64:
            # Matches the schema constraint on AuditVerdict.skill_hash —
            # enforce it at the client too so we never log a partial hash.
            raise ValueError(
                f"skill_hash must be 64-char sha256 hex, got len={len(skill_hash)}"
            )

        kh = _key_hash(
            skill_id=skill_id,
            skill_hash=skill_hash,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            system=system,
            user=user,
        )

        cached = self._cache.get(kh)
        if cached is not None:
            # Cache hits and replay serves have elapsed_s=0.0 by design —
            # the original live-run latency isn't stored in the replay
            # log, and reporting "how long it took me to read a line
            # from disk" would be misleading vs §10.1's intent.
            return self._response_from_entry(cached, cache_hit=True, elapsed_s=0.0)

        if self._mode == "replay":
            raise ReplayMiss(
                f"replay miss for skill={skill_id} model={model} key_hash={kh[:16]}…"
            )

        # Pre-call budget check. Cheap and short-lived — don't hold the
        # cost lock across the API round-trip. Racing is acceptable:
        # in-flight coroutines complete (policy, ARCHITECTURE §5.5).
        async with self._cost_lock:
            if self._cumulative_usd >= self._usd_ceiling:
                raise CostCeilingExceeded(
                    f"run_id={self._run_id}: cumulative USD "
                    f"{self._cumulative_usd:.4f} ≥ ceiling {self._usd_ceiling:.2f}"
                )

        # Measure dispatch time (API round-trip + tenacity retries, but
        # not semaphore-wait time — that's queuing, not work). Monotonic
        # so system clock changes don't corrupt the measurement.
        async with self._sem:
            t0 = time.monotonic()
            message = await self._dispatch(
                system=system,
                user=user,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            elapsed_s = time.monotonic() - t0

        response_text = _extract_text(message)
        usage = getattr(message, "usage", None)
        if usage is None:
            raise RuntimeError("Anthropic response missing .usage — cannot account cost")
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cost = estimate_cost_usd(model, input_tokens, output_tokens)

        async with self._cost_lock:
            self._cumulative_usd += cost

        ts = datetime.now(UTC).isoformat(timespec="seconds")
        # call_id is globally unique per call; derived from the hash
        # for stability across replays, plus a short random suffix so
        # two runs that produce identical keys (post-cache-invalidation)
        # still have distinguishable ids in the log.
        call_id = f"{kh[:12]}-{secrets.token_hex(4)}"
        prompt_blob = _canonical_prompt(system, user)

        entry: dict[str, Any] = {
            "call_id": call_id,
            "key_hash": kh,
            "skill_id": skill_id,
            "skill_hash": skill_hash,
            "model": model,
            "temperature": float(temperature),
            "prompt": prompt_blob,
            "response": response_text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            # cost_usd is an extension of the ARCHITECTURE §5.2 field
            # list — observability wants it present in-log so the
            # manifest verifier can be kept single-file.
            "cost_usd": cost,
            "timestamp": ts,
        }

        async with self._append_lock:
            await asyncio.to_thread(_append_jsonl, self._log_path, entry)
            self._cache[kh] = entry

        _log.info(
            "claude_call",
            extra={
                "event": "claude_call",
                "run_id": self._run_id,
                "skill_id": skill_id,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost,
                "cumulative_usd": self._cumulative_usd,
                "elapsed_s": elapsed_s,
                "cache_hit": False,
            },
        )

        return self._response_from_entry(entry, cache_hit=False, elapsed_s=elapsed_s)

    # -- Internals -----------------------------------------------------------

    async def _dispatch(
        self,
        *,
        system: str,
        user: str,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> Any:
        """Call the Messages API with tenacity-backed retries.

        Only retriable error classes (:data:`_RETRIABLE_ERRORS`) are
        caught; everything else propagates as-is so a caller sees the
        root cause of a client error (e.g. malformed request) without
        misleading retry noise.
        """
        if self._sdk_client is None:
            self._sdk_client = anthropic.AsyncAnthropic()

        # ``reraise=True`` makes tenacity raise the underlying exception
        # after the final attempt, so no ``RetryError`` handler is
        # needed. The loop always returns on success or raises on the
        # final attempt — the function is total by construction.
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._retry_attempts),
            wait=wait_exponential_jitter(initial=1.0, max=30.0),
            retry=retry_if_exception_type(_RETRIABLE_ERRORS),
            reraise=True,
        ):
            with attempt:
                return await self._sdk_client.messages.create(
                    model=model,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
        raise AssertionError("unreachable: AsyncRetrying with reraise=True")  # pragma: no cover

    @staticmethod
    def _response_from_entry(
        entry: Mapping[str, Any], *, cache_hit: bool, elapsed_s: float
    ) -> ClaudeResponse:
        return ClaudeResponse(
            call_id=entry["call_id"],
            key_hash=entry["key_hash"],
            skill_id=entry["skill_id"],
            skill_hash=entry["skill_hash"],
            model=entry["model"],
            temperature=float(entry["temperature"]),
            prompt=entry["prompt"],
            response=entry["response"],
            input_tokens=int(entry["input_tokens"]),
            output_tokens=int(entry["output_tokens"]),
            cost_usd=float(entry.get("cost_usd", 0.0)),
            timestamp=entry["timestamp"],
            cache_hit=cache_hit,
            elapsed_s=elapsed_s,
        )
