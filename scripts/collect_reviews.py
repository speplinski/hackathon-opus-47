"""Collect Duolingo reviews from Google Play — produce `data/raw/corpus.jsonl`.

See CONTEXT_DUOLINGO.md §2 for the authoritative corpus-selection criteria;
this script is the faithful implementation.

What lands on disk
------------------
One JSONL per review matching :class:`auditable_design.schemas.RawReview`, plus
a sha256 manifest (`sha256sum`-compatible format) next to it.

Selection (defaults match CONTEXT §2)
-------------------------------------
* Google Play only (secondary App Store source deferred).
* Language: `en`.
* Date window: 2026-04-01 ≤ at < 2026-04-22 (inclusive of the 21st, UTC).
  Three-week steady-state window — chosen because Google Play sorts NEWEST-
  first and review volume makes a full-year backfill impractical. See
  CONTEXT §2 for framing.
* Length: 80 ≤ len(text) ≤ 4000 chars.
* Target: 600 reviews; stratified 60/40 between 1–3★ (signal) and 4–5★ (control).
* Deterministic sampling — seed 42 — so the full 600 is a superset of the
  first pilot 60.

PII handling (SECURITY.md V-09)
-------------------------------
Raw `userName` is never written. We only store
``author_hash = sha256(salt || userName)`` where ``salt`` is a 32-byte value
kept in `.env` (`DUOLINGO_REVIEW_SALT=<hex>`) and never committed. First run
generates and persists a salt if `.env` has none. Refusing to overwrite an
existing salt is deliberate — overwriting would invalidate all previously
captured hashes and break forward-traceability from the author side.

Idempotency
-----------
Re-running the script reads any existing `corpus.jsonl`, gathers the canonical
review IDs already captured, and fills only the delta until the target is hit.
Fresh-run is equivalent to delete-file-then-run.

Not here (by design, pointable reason)
--------------------------------------
* No network retry. `google-play-scraper` already retries HTTP; the sampling
  tolerates an occasional missing page.
* No language detection. We ask Google Play for `lang='en'` and trust that;
  the downstream quarantine grep (PLAN V-09) catches whatever slips through.
* App Store fetch path. CONTEXT calls it "secondary, for parity check" —
  deferred until a parity check is actually scheduled.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import random
import re
import sys
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from auditable_design.schemas import RawReview

_log = logging.getLogger("collect_reviews")

# --- Public constants --------------------------------------------------------

# Defaults derived from CONTEXT_DUOLINGO.md §2. Changing these changes the
# corpus contract — do it through a doc edit first.
DEFAULT_APP_ID: str = "com.duolingo"
DEFAULT_TARGET: int = 600
DEFAULT_SEED: int = 42
DEFAULT_DATE_FROM = datetime(2026, 4, 1, tzinfo=UTC)
DEFAULT_DATE_TO = datetime(2026, 4, 22, tzinfo=UTC)  # half-open — see _passes_filters
MIN_LEN: int = 80
MAX_LEN: int = 4000
LOW_STARS: frozenset[int] = frozenset({1, 2, 3})
HIGH_STARS: frozenset[int] = frozenset({4, 5})
LOW_RATIO: float = 0.60  # 1–3★ share of the target

SALT_ENV_KEY: str = "DUOLINGO_REVIEW_SALT"
SALT_BYTES: int = 32


# --- Datatypes ---------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class RawPayload:
    """Shape of what the fetcher yields.

    A thin, strictly-typed view over `google-play-scraper`'s dict return, so
    we don't pass `Any`-soup into the transform layer. Tests construct this
    directly; the production fetcher adapts Google's dicts.
    """

    review_id_external: str
    user_name: str
    content: str
    score: int
    at: datetime
    app_version: str | None


# A fetcher yields `RawPayload`s — pure data, no side effects on the caller.
Fetcher = Callable[[], Iterator[RawPayload]]


# --- Salt management ---------------------------------------------------------


_SALT_LINE_RE = re.compile(rf"^\s*{re.escape(SALT_ENV_KEY)}\s*=\s*(?P<val>.*?)\s*$")


def load_or_create_salt(env_path: Path, *, rng: random.Random | None = None) -> bytes:
    """Return the persistent 32-byte salt; create and persist one if missing.

    Refuses to overwrite a non-empty salt in `.env` — that would silently
    re-key every hash and break V-09's "forward-traceability possible only
    for the author" property.

    `rng` is injectable so tests get a deterministic salt without monkey-
    patching `os.urandom`. Production uses `secrets.token_bytes` via the
    default `None` branch.
    """
    existing = _read_salt_value(env_path) if env_path.exists() else None
    if existing:
        try:
            raw = bytes.fromhex(existing)
        except ValueError as e:
            raise RuntimeError(
                f"{env_path}: {SALT_ENV_KEY} is not valid hex — refuse to regenerate, fix the file by hand"
            ) from e
        if len(raw) != SALT_BYTES:
            raise RuntimeError(f"{env_path}: {SALT_ENV_KEY} is {len(raw)} bytes, expected {SALT_BYTES}")
        return raw

    if rng is None:
        import secrets

        salt = secrets.token_bytes(SALT_BYTES)
    else:
        salt = bytes(rng.getrandbits(8) for _ in range(SALT_BYTES))
    _append_env_value(env_path, SALT_ENV_KEY, salt.hex())
    _log.warning(
        "Generated new %s in %s — this file is gitignored. Back it up; "
        "rotating the salt invalidates every existing author_hash.",
        SALT_ENV_KEY,
        env_path,
    )
    return salt


def _read_salt_value(path: Path) -> str | None:
    """Read the salt value from `.env`-style file, or return None.

    Skips comment lines and treats a blank `DUOLINGO_REVIEW_SALT=` as "not
    set" so the template in `.env.example` does not trip the hex validator.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _SALT_LINE_RE.match(line)
        if m and m.group("val"):
            # Strip optional matching quotes.
            val = m.group("val")
            if len(val) >= 2 and val[0] == val[-1] and val[0] in {'"', "'"}:
                val = val[1:-1]
            return val
    return None


def _append_env_value(path: Path, key: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = "" if not path.exists() or path.read_text(encoding="utf-8").endswith("\n") else "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{prefix}{key}={value}\n")


def author_hash(salt: bytes, user_name: str) -> str:
    """Compute `sha256(salt || userName)` hex — the canonical author_hash."""
    return hashlib.sha256(salt + user_name.encode("utf-8")).hexdigest()


# --- Filtering ---------------------------------------------------------------


def _passes_filters(p: RawPayload, *, date_from: datetime, date_to: datetime) -> bool:
    """Return True iff the payload meets CONTEXT §2 intake criteria.

    Half-open date range `[date_from, date_to)` — keeps reasoning off the
    "is 23:59:59 included?" question when callers pass `date_to = end+1 day`.
    """
    if p.at < date_from or p.at >= date_to:
        return False
    if p.score not in LOW_STARS and p.score not in HIGH_STARS:
        return False
    if len(p.content) < MIN_LEN or len(p.content) > MAX_LEN:
        return False
    return True


# --- Transform ---------------------------------------------------------------


def to_review(payload: RawPayload, *, salt: bytes) -> RawReview:
    """Build a validated `RawReview` from a `RawPayload`.

    `review_id` follows ARCHITECTURE.md §4.1:
        `sha1(source + author_hash + timestamp)`
    We use `isoformat()` for the timestamp to get a deterministic string;
    tz-aware `datetime` with `UTC` → always the same bytes.

    `lang="en"` is hardcoded as a contract with the fetcher (CONTEXT §2
    fixes the corpus to English), not a detection — the downstream grep
    quarantine in PLAN V-09 catches whatever slips through.
    """
    ah = author_hash(salt, payload.user_name)
    at_utc = payload.at if payload.at.tzinfo else payload.at.replace(tzinfo=UTC)
    rid_seed = f"google_play{ah}{at_utc.isoformat()}"
    review_id = hashlib.sha1(rid_seed.encode("utf-8"), usedforsecurity=False).hexdigest()
    return RawReview(
        review_id=review_id,
        source="google_play",
        author_hash=ah,
        timestamp_utc=at_utc,
        rating=payload.score,
        text=payload.content,
        lang="en",
        app_version=payload.app_version,
    )


# --- Sampling ----------------------------------------------------------------


def stratified_sample(
    candidates: Iterable[RawReview],
    *,
    target: int,
    seed: int,
    low_ratio: float = LOW_RATIO,
) -> list[RawReview]:
    """Return up to `target` reviews, stratified low / high by rating.

    The per-bucket shuffle uses `random.Random(seed)` — no global state, no
    cryptographic requirement. If a bucket has fewer items than its quota,
    the opposite bucket absorbs the shortfall (CONTEXT §2 wants *up to* 600,
    not "fail if Google didn't serve us 360 low-star reviews this week").
    Output is sorted by `review_id` for a stable on-disk order.
    """
    low: list[RawReview] = []
    high: list[RawReview] = []
    for r in candidates:
        (low if r.rating in LOW_STARS else high).append(r)

    want_low = round(target * low_ratio)
    want_high = target - want_low

    rng = random.Random(seed)  # noqa: S311 — sampling, not crypto
    rng.shuffle(low)
    rng.shuffle(high)

    # Absorb shortfall across buckets.
    take_low = min(len(low), want_low)
    take_high = min(len(high), want_high)
    slack = target - take_low - take_high
    if slack > 0:
        if len(low) > take_low:
            extra = min(slack, len(low) - take_low)
            take_low += extra
            slack -= extra
        if slack > 0 and len(high) > take_high:
            take_high += min(slack, len(high) - take_high)

    picked = low[:take_low] + high[:take_high]
    picked.sort(key=lambda r: r.review_id)
    return picked


# --- IO ----------------------------------------------------------------------


def _read_existing_ids(path: Path) -> set[str]:
    """Best-effort: return review_ids already captured in `path`.

    Idempotency is the reason this exists; it tolerates a partially-written
    file (a malformed tail line is logged and dropped from the id set). Only
    parses the ``review_id`` field — strictly less work than
    :func:`_read_existing_records`, which rebuilds full pydantic models.
    """
    if not path.exists():
        return set()
    ids: set[str] = set()
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                ids.add(json.loads(line)["review_id"])
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                _log.warning("dropping malformed line %s:%d: %s", path, lineno, e)
    return ids


def _read_existing_records(path: Path) -> list[RawReview]:
    """Parse existing JSONL as a list of :class:`RawReview`.

    Used by :func:`main` on rerun so that previously-captured records are
    preserved — a rerun must NEVER destroy prior work. Malformed or
    schema-incompatible lines are logged and dropped (same tolerance as
    :func:`_read_existing_ids`) rather than aborting the run.
    """
    if not path.exists():
        return []
    out: list[RawReview] = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(RawReview.model_validate_json(line))
            except (json.JSONDecodeError, ValidationError) as e:
                _log.warning("dropping malformed line %s:%d: %s", path, lineno, e)
    return out


def _write_corpus_atomic(records: list[RawReview], out_path: Path) -> str:
    """Write `records` as JSONL via tmp + fsync + rename. Return sha256 hex.

    Atomic replace so a crash mid-write leaves the old corpus intact.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    hasher = hashlib.sha256()
    with open(tmp_path, "wb") as f:
        for r in records:
            line = r.model_dump_json().encode("utf-8") + b"\n"
            f.write(line)
            hasher.update(line)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, out_path)
    _fsync_dir(out_path.parent)
    return hasher.hexdigest()


def _fsync_dir(dir_path: Path) -> None:
    """fsync a directory on POSIX; silently no-op on Windows."""
    try:
        fd = os.open(str(dir_path), os.O_DIRECTORY)
    except (OSError, AttributeError):
        return  # Windows / unusual FS; best-effort.
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_manifest(corpus_path: Path, sha_hex: str, manifest_path: Path) -> None:
    """Write a `sha256sum`-compatible manifest (`<hex>  <filename>\\n`)."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{sha_hex}  {corpus_path.name}\n"
    manifest_path.write_text(line, encoding="utf-8")


# --- Orchestration -----------------------------------------------------------


def collect(
    *,
    fetcher: Fetcher,
    salt: bytes,
    target: int = DEFAULT_TARGET,
    seed: int = DEFAULT_SEED,
    date_from: datetime = DEFAULT_DATE_FROM,
    date_to: datetime = DEFAULT_DATE_TO,
    existing_ids: Iterable[str] = (),
) -> list[RawReview]:
    """Pure pipeline: fetcher → filter → hash → stratified sample.

    Returns the picked records. Does not write; the caller owns IO so this
    function is trivially testable without touching disk.
    """
    seen_ids = set(existing_ids)
    kept: list[RawReview] = []
    dropped_validation = 0
    for payload in fetcher():
        if not _passes_filters(payload, date_from=date_from, date_to=date_to):
            continue
        try:
            review = to_review(payload, salt=salt)
        except ValidationError as e:
            dropped_validation += 1
            _log.warning("dropping review via pydantic: %s", e)
            continue
        if review.review_id in seen_ids:
            continue
        seen_ids.add(review.review_id)
        kept.append(review)

    if dropped_validation:
        _log.info("dropped %d payloads on pydantic validation", dropped_validation)

    picked = stratified_sample(kept, target=target, seed=seed)
    _log.info(
        "collected %d candidates → %d picked (target %d, low %d, high %d)",
        len(kept),
        len(picked),
        target,
        sum(1 for r in picked if r.rating in LOW_STARS),
        sum(1 for r in picked if r.rating in HIGH_STARS),
    )
    return picked


# --- Production fetcher ------------------------------------------------------


def google_play_fetcher(
    app_id: str = DEFAULT_APP_ID,
    *,
    lang: str = "en",
    country: str = "us",
    max_pages: int = 50,
    page_size: int = 200,
) -> Fetcher:
    """Return a `Fetcher` that paginates Google Play via google-play-scraper.

    The lib is optional at import time; this thunk-style factory lets tests
    that never fetch real data skip the import entirely.
    """

    def _iter() -> Iterator[RawPayload]:
        from google_play_scraper import Sort, reviews

        token: Any = None
        for _ in range(max_pages):
            result, token = reviews(
                app_id,
                lang=lang,
                country=country,
                sort=Sort.NEWEST,
                count=page_size,
                continuation_token=token,
            )
            for r in result:
                at = r.get("at")
                if not isinstance(at, datetime):
                    continue
                if at.tzinfo is None:
                    at = at.replace(tzinfo=UTC)
                yield RawPayload(
                    review_id_external=str(r.get("reviewId", "")),
                    user_name=str(r.get("userName", "")),
                    content=str(r.get("content", "")),
                    score=int(r.get("score", 0)),
                    at=at,
                    app_version=(str(r["appVersion"]) if r.get("appVersion") else None),
                )
            if token is None:
                break

    return _iter


# --- CLI ---------------------------------------------------------------------


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _resolve_repo_root() -> Path:
    """Resolve the repo root by walking upward from this file."""
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("cannot locate repo root (no pyproject.toml above this file)")


def main(argv: list[str] | None = None) -> int:
    import argparse

    repo_root = _resolve_repo_root()
    parser = argparse.ArgumentParser(description="Collect Duolingo Google Play reviews.")
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--out-path",
        type=Path,
        default=repo_root / "data/raw/corpus.jsonl",
        help="Output JSONL (default: data/raw/corpus.jsonl).",
    )
    parser.add_argument(
        "--env-path",
        type=Path,
        default=repo_root / ".env",
        help="Where the salt lives (default: .env at repo root, gitignored).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=50,
        help="Max Google Play pages to paginate (safety cap).",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    salt = load_or_create_salt(args.env_path)

    # Preserve anything already on disk (B-01): a rerun must never destroy
    # prior work. We read full records, not just ids, so we can merge them
    # back into the new write.
    existing_records = _read_existing_records(args.out_path)
    existing_ids = {r.review_id for r in existing_records}
    if existing_records:
        _log.info(
            "%d reviews already in %s — will preserve and top up to --target",
            len(existing_records),
            args.out_path,
        )

    # Cap new fetch so total converges to --target, not 2×target on rerun.
    effective_target = max(0, args.target - len(existing_records))
    if effective_target == 0:
        _log.info(
            "corpus already at or above target (%d ≥ %d) — rerun is a no-op fetch",
            len(existing_records),
            args.target,
        )
        new_records: list[RawReview] = []
    else:
        fetcher = google_play_fetcher(max_pages=args.max_pages)
        new_records = collect(
            fetcher=fetcher,
            salt=salt,
            target=effective_target,
            seed=args.seed,
            existing_ids=existing_ids,
        )

    combined = [*existing_records, *new_records]
    combined.sort(key=lambda r: r.review_id)

    sha = _write_corpus_atomic(combined, args.out_path)
    # Per CONTEXT §2 the manifest sits next to the corpus as
    # `corpus.manifest.sha256` (NOT `corpus.jsonl.manifest.sha256`).
    manifest_path = args.out_path.parent / f"{args.out_path.stem}.manifest.sha256"
    _write_manifest(args.out_path, sha, manifest_path)

    _log.info(
        "wrote %d records (%d preserved + %d new) → %s (sha256 %s)",
        len(combined),
        len(existing_records),
        len(new_records),
        args.out_path,
        sha,
    )
    _log.info("manifest → %s", manifest_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
