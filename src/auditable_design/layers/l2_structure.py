"""Layer 2 — per-review structure-of-complaint graph extraction.

Given a UX-relevant :class:`RawReview`, produce a :class:`ComplaintGraph`
(3-7 typed nodes + typed edges, every node anchored to a verbatim
substring of the source review). Writes graphs to
``data/derived/l2_graphs.jsonl`` and quarantined reviews (thin, padded,
or hallucinated) to ``data/quarantine/l2_thin.jsonl``.

Skill contract lives in ``skills/structure-of-complaint/SKILL.md`` and
is loaded at import time as :data:`SYSTEM_PROMPT`. The model produces
only ``{node_id, node_type, verbatim_quote}`` per node — this layer
computes ``quote_start``/``quote_end`` via ``str.find`` on the source
text (Option B per the 2026-04-22 L2 authoring session: the model
stays simple; the layer catches hallucinations by rejecting quotes
that aren't literal substrings).

Quarantine vs error
-------------------
Two distinct failure classes, persisted differently:

* **Quarantine** — the model followed the skill correctly but the
  graph isn't usable at full-pipeline strength: under-minimum (<3
  nodes per skill's thin-review routing), substring-containment
  padding (Fix C from the pilot), or one or more verbatim_quote values
  not found in the source (hallucination). Written to
  ``data/quarantine/l2_thin.jsonl`` with a typed ``reason``; the
  review_id is retained so L3 clustering can still reference the
  source text, just not the graph.
* **Error** — the layer could not produce any output: parse failure,
  vocabulary violation, LLM exception, schema validation error from
  Pydantic. Collected in-memory as ``failures`` for the operator to
  inspect and rerun; NOT persisted.

Idempotency
-----------
Both output files contribute to the "already processed" set on rerun.
A review_id that appears in either is skipped on the next invocation.
Changing ``SYSTEM_PROMPT`` (i.e. editing SKILL.md) changes the
skill_hash which invalidates the replay cache for prior runs —
intentional, a skill edit is a semantic change.

Not here (pointable reason for absence)
---------------------------------------
* No auto-repair of padded or hallucinated graphs. Dropping the
  offending node and trying again would paper over a model drift we
  want to see. Quarantine surfaces the failure; the operator decides
  whether to adjust SKILL.md or accept the rate.
* No retry on parse/vocab failure. ``claude_client`` already retries
  transient 5xx/rate-limit errors; a malformed JSON body with
  ``temperature=0`` + JSON-only instruction is a semantic failure,
  not a transient one. Fix the prompt, don't loop.
* No per-node confidence. The model already throws confidence signal
  away when it emits a JSON graph — asking for it back would require
  a second call or a structured-output contract we don't have. L3
  aggregates at cluster level where individual-node noise washes out.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from auditable_design.claude_client import Client
from auditable_design.prompt_builder import wrap_user_text
from auditable_design.schemas import (
    SCHEMA_VERSION,
    ComplaintEdge,
    ComplaintGraph,
    ComplaintNode,
    ClassifiedReview,
    RawReview,
    SchemaValidationError,
    validate_complaint_graph_against_source,
)
from auditable_design.storage import hash_file, read_jsonl, write_jsonl_atomic

__all__ = [
    "LAYER_NAME",
    "MAX_TOKENS",
    "MIN_NODES",
    "MAX_NODES",
    "MODEL",
    "NODE_TYPES",
    "RELATION_TYPES",
    "SKILL_ID",
    "SYSTEM_PROMPT",
    "TEMPERATURE",
    "L2Outcome",
    "ParseError",
    "QuarantineReason",
    "build_user_message",
    "compute_node_offsets",
    "extract_graph",
    "has_substring_containment",
    "load_existing_graphs",
    "load_existing_thin",
    "load_ux_relevant_reviews",
    "main",
    "parse_response",
    "skill_hash",
    "structure_batch",
    "structure_one",
]

_log = logging.getLogger("l2_structure")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKILL_ID: str = "structure-of-complaint"
LAYER_NAME: str = "l2_structure"

# Opus 4.7 per ADR-009 §"Proposed model mapping". L2's verbatim-quote
# constraint is fragile (the pipeline rejects hallucinated spans at
# ingest — §4.3 P1); Opus 4.7's stronger constraint satisfaction is
# worth the premium over Sonnet here. No L2 pilot has been run yet —
# if a future three-way test shows Opus 4.6 matches or beats 4.7 on
# substring-fidelity + typing accuracy, bump this and update ADR-009.
# Opus 4.7 doesn't accept custom sampling params; claude_client drops
# them via ``_omits_sampling_params`` (ADR-009 §Known gotchas).
MODEL: str = "claude-opus-4-7"
TEMPERATURE: float = 0.0
# Response shape: up to 7 nodes × ~60 chars quote + metadata, plus up
# to ~10 edges × ~40 chars. 1024 gives comfortable headroom for the
# worst case without paying for long-form prose the skill forbids.
MAX_TOKENS: int = 1024

# Mirror the schema's Literal vocabularies. Duplicated here — not
# re-exported — so parse_response can enum-check without importing
# the Literal objects themselves (which aren't runtime-iterable).
NODE_TYPES: frozenset[str] = frozenset(
    {"pain", "expectation", "triggered_element", "workaround", "lost_value"}
)
RELATION_TYPES: frozenset[str] = frozenset(
    {"triggers", "violates_expectation", "compensates_for", "correlates_with"}
)

# Schema contract (§4.3): 3-7 nodes. A model response with fewer is
# routed to quarantine per SKILL's thin-review rule; more is a bug.
MIN_NODES: int = 3
MAX_NODES: int = 7

# Default paths — relative to repo root, resolved in main().
DEFAULT_CORPUS = Path("data/raw/corpus.jsonl")
DEFAULT_CLASSIFIED = Path("data/derived/l1_classified.jsonl")
DEFAULT_GRAPHS = Path("data/derived/l2_graphs.jsonl")
DEFAULT_QUARANTINE = Path("data/quarantine/l2_thin.jsonl")

QuarantineReason = Literal[
    "under_minimum_nodes",
    "over_maximum_nodes",
    "substring_containment",
    "hallucination",
    "schema_violation",
]


# ---------------------------------------------------------------------------
# System prompt — loaded from SKILL.md
# ---------------------------------------------------------------------------


def _resolve_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("cannot locate repo root (no pyproject.toml above this file)")


def _load_skill_body() -> str:
    """Read ``skills/structure-of-complaint/SKILL.md`` and strip YAML frontmatter.

    The frontmatter (``---\\nname:…\\ndescription:…\\n---``) is metadata
    for Claude Code's skill loader, not guidance for the model. We send
    only the body as the system prompt.

    Raises at import time if the file is missing — the layer cannot
    function without its skill.
    """
    repo_root = _resolve_repo_root()
    path = repo_root / "skills" / SKILL_ID / "SKILL.md"
    if not path.exists():
        raise RuntimeError(
            f"{LAYER_NAME}: SKILL.md not found at {path}; layer cannot initialise"
        )
    content = path.read_text(encoding="utf-8")
    # Strip leading YAML frontmatter block. Format: opening "---\n"
    # then keys, then a closing "---\n". Anything else → pass through
    # untouched (safer than aggressive stripping).
    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end != -1:
            content = content[end + len("\n---\n") :]
    return content.strip()


# Changing SKILL.md changes SYSTEM_PROMPT which changes skill_hash
# which invalidates the replay cache for prior L2 runs — intentional.
SYSTEM_PROMPT: str = _load_skill_body()


# ---------------------------------------------------------------------------
# Skill hash
# ---------------------------------------------------------------------------


def skill_hash() -> str:
    """sha256 of :data:`SYSTEM_PROMPT` — the identity of the L2 brain.

    Included in every :meth:`claude_client.Client.call` invocation so
    the replay cache is keyed on the exact prompt Claude saw. Any
    edit to SKILL.md produces a different hash → different key → no
    silent reuse of stale responses.
    """
    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Prompt build
# ---------------------------------------------------------------------------


def build_user_message(review: RawReview) -> str:
    """Render the per-review user message.

    The review text is wrapped in ``<user_review id="…">…</user_review>``
    (ADR-010 injection guard). The rating is NOT included: L2 focuses
    on structural extraction, not sentiment — rating would bias the
    model toward over-emitting complaint content for a 1-star review
    or under-emitting for a 4-star review. The skill reads what's in
    the text.
    """
    wrapped = wrap_user_text(review.text, review_id=review.review_id)
    if wrapped.contained_markup:
        # HTML-sensitive chars in the source text get escaped inside
        # the wrapper; verbatim-quote checks against the original text
        # will fail on any quote that spans the escaped character.
        # Not an error here — the hallucination handler downstream
        # catches it — but worth a debug breadcrumb.
        _log.debug(
            "review %s contains HTML-sensitive chars; verbatim-quote may miss",
            review.review_id[:8],
        )
    return wrapped.wrapped


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class ParseError(ValueError):
    """Raised when a Claude response cannot be coerced into the L2 contract.

    Distinct from :class:`schemas.SchemaValidationError`: ParseError
    covers JSON-level and vocabulary-level violations, SchemaValidationError
    covers Pydantic-level constraint violations after we've built a
    ``ComplaintGraph`` instance.
    """


def _extract_json_objects(text: str) -> list[dict[str, Any]]:
    """Find every top-level JSON object in ``text`` via ``raw_decode``.

    Tolerates the "think out loud" pattern observed on Opus 4.6 and
    Sonnet 4.6 L2 responses: first ``{…}`` in a code fence, then
    prose like "Wait, let me reconsider — …", then a second ``{…}``.
    The previous greedy-regex parser concatenated both objects with
    intervening prose and raised ``json.JSONDecodeError`` on the
    malformed whole. ``raw_decode`` parses one object and reports
    where it ended, so we can walk the string and collect all of
    them.

    Non-dict top-level values (lists, primitives) are dropped —
    ``parse_response`` only accepts objects.
    """
    objects: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    pos = 0
    n = len(text)
    while pos < n:
        idx = text.find("{", pos)
        if idx < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            pos = idx + 1
            continue
        if isinstance(obj, dict):
            objects.append(obj)
        pos = idx + end
    return objects


def parse_response(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract ``(nodes, edges)`` as raw dicts from a Claude response.

    Validates JSON structure + closed vocabularies + key sets. Does NOT
    compute ``quote_start``/``quote_end`` (needs the source text — see
    :func:`compute_node_offsets`) and does NOT enforce the 3-7 node
    bound (needs to route under-minimum to quarantine — see
    :func:`extract_graph`).

    When the response contains multiple JSON objects (Opus 4.6 /
    Sonnet 4.6 sometimes emit a first attempt, prose reconsidering
    it, then a revised attempt), the **last** parseable object wins —
    i.e. the model's final answer rather than the draft it corrected.

    Raises:
        ParseError: On malformed JSON, wrong key sets, unknown
            ``node_type`` or ``relation``, duplicate ``node_id``,
            self-loop, or edge referencing a missing node_id.
    """
    objects = _extract_json_objects(text)
    if not objects:
        raise ParseError(f"no JSON object found in response: {text!r}")
    data = objects[-1]
    if not isinstance(data, dict):
        raise ParseError(f"expected JSON object, got {type(data).__name__}")

    allowed = {"nodes", "edges"}
    actual = set(data.keys())
    extra = actual - allowed
    if extra:
        raise ParseError(f"unexpected top-level keys: {sorted(extra)}")
    if "nodes" not in actual:
        raise ParseError("missing required top-level key: 'nodes'")

    raw_nodes = data["nodes"]
    if not isinstance(raw_nodes, list):
        raise ParseError(f"nodes must be a list, got {type(raw_nodes).__name__}")
    # `edges` is optional at the top level. Per SKILL.md the relation
    # vocabulary describes when edges *should* appear, but explicitly
    # allows "zero or more" — and in practice Opus will sometimes emit
    # `{"nodes": [...]}` without the key for short reviews that carry no
    # clear causal link. Treat missing as an empty list: semantically
    # equivalent, keeps the pipeline from dropping otherwise-valid rows.
    raw_edges = data.get("edges", [])
    if not isinstance(raw_edges, list):
        raise ParseError(f"edges must be a list, got {type(raw_edges).__name__}")

    nodes: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for i, n in enumerate(raw_nodes):
        if not isinstance(n, dict):
            raise ParseError(f"nodes[{i}] must be an object, got {type(n).__name__}")
        nkeys = set(n.keys())
        if nkeys != {"node_id", "node_type", "verbatim_quote"}:
            raise ParseError(
                f"nodes[{i}] key set mismatch: got {sorted(nkeys)}, "
                "expected ['node_id', 'node_type', 'verbatim_quote']"
            )
        nid = n["node_id"]
        if not isinstance(nid, str) or not nid:
            raise ParseError(f"nodes[{i}].node_id must be a non-empty string")
        if nid in seen_ids:
            raise ParseError(f"duplicate node_id {nid!r}")
        seen_ids.add(nid)
        ntype = n["node_type"]
        if ntype not in NODE_TYPES:
            raise ParseError(
                f"nodes[{i}].node_type {ntype!r} not in closed vocabulary {sorted(NODE_TYPES)}"
            )
        quote = n["verbatim_quote"]
        if not isinstance(quote, str) or not quote:
            raise ParseError(f"nodes[{i}].verbatim_quote must be a non-empty string")
        nodes.append({"node_id": nid, "node_type": ntype, "verbatim_quote": quote})

    edges: list[dict[str, Any]] = []
    for i, e in enumerate(raw_edges):
        if not isinstance(e, dict):
            raise ParseError(f"edges[{i}] must be an object, got {type(e).__name__}")
        ekeys = set(e.keys())
        if ekeys != {"src", "dst", "relation"}:
            raise ParseError(
                f"edges[{i}] key set mismatch: got {sorted(ekeys)}, "
                "expected ['src', 'dst', 'relation']"
            )
        src = e["src"]
        dst = e["dst"]
        rel = e["relation"]
        if not isinstance(src, str) or src not in seen_ids:
            raise ParseError(f"edges[{i}].src {src!r} does not reference a known node")
        if not isinstance(dst, str) or dst not in seen_ids:
            raise ParseError(f"edges[{i}].dst {dst!r} does not reference a known node")
        if src == dst:
            raise ParseError(f"edges[{i}] self-loop on {src!r}")
        if rel not in RELATION_TYPES:
            raise ParseError(
                f"edges[{i}].relation {rel!r} not in closed vocabulary {sorted(RELATION_TYPES)}"
            )
        edges.append({"src": src, "dst": dst, "relation": rel})

    return nodes, edges


# ---------------------------------------------------------------------------
# Post-processing — substring containment + offset computation
# ---------------------------------------------------------------------------


def has_substring_containment(nodes: list[dict[str, Any]]) -> tuple[bool, str]:
    """Detect padding where one node's quote is fully contained in another.

    Per SKILL.md Fix C (committed cbf06e0 2026-04-22): a node whose
    ``verbatim_quote`` is a substring of another node's quote is a
    padding failure — the two nodes are almost certainly splitting
    one concept, or the shorter quote is a forced extra span.

    Returns:
        ``(found, detail)`` — ``found`` is True on any containment;
        ``detail`` names the offending pair (shortest pair first) or
        empty string if none.
    """
    quotes = [(n["node_id"], n["verbatim_quote"]) for n in nodes]
    for i, (id_a, qa) in enumerate(quotes):
        for id_b, qb in quotes[i + 1 :]:
            # Identical quotes also trip the substring check (a ⊆ b
            # and b ⊆ a). Distinguish them for clearer quarantine
            # detail — duplicated spans are a different class of
            # padding but rejected the same way.
            if qa == qb:
                return True, f"identical quotes {id_a}=={id_b}: {qa!r}"
            if qa in qb:
                return True, f"{id_a!r}.quote ⊂ {id_b!r}.quote: {qa!r} ⊂ {qb!r}"
            if qb in qa:
                return True, f"{id_b!r}.quote ⊂ {id_a!r}.quote: {qb!r} ⊂ {qa!r}"
    return False, ""


def compute_node_offsets(
    nodes: list[dict[str, Any]],
    source_text: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Locate each node's verbatim_quote in the source and attach offsets.

    Uses ``str.find`` on the first occurrence. For reviews where the
    same phrase repeats (e.g. the word "no" appearing twice), we take
    the first match — the model produced the quote, not the offset, so
    there's no "intended" index to recover. If a later layer needs
    token-level disambiguation, the quote itself remains the authority.

    Returns:
        ``(nodes_with_offsets, missing_ids)`` — ``nodes_with_offsets``
        mirrors the input with ``quote_start``/``quote_end`` keys added
        for nodes whose quote was found; ``missing_ids`` lists node_ids
        whose quote was NOT found in ``source_text`` (hallucinations).
    """
    out: list[dict[str, Any]] = []
    missing: list[str] = []
    for n in nodes:
        quote = n["verbatim_quote"]
        start = source_text.find(quote)
        if start < 0:
            missing.append(n["node_id"])
            continue
        out.append(
            {
                **n,
                "quote_start": start,
                "quote_end": start + len(quote),
            }
        )
    return out, missing


# ---------------------------------------------------------------------------
# Outcome dataclass — the single shape returned from per-review processing
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class L2Outcome:
    """Result of structuring one review.

    Exactly one of ``graph`` / ``quarantine_record`` is non-None.

    * ``graph`` — full :class:`ComplaintGraph`, validated against
      source text, ready for L3 ingest.
    * ``quarantine_record`` — dict with ``review_id`` + ``reason`` +
      ``node_count`` + ``detail`` + ``processed_at``; the review is
      routed to ``data/quarantine/l2_thin.jsonl`` and excluded from
      L3 graph-consuming flows. Source text is still available to L3
      via the corpus.
    """

    review_id: str
    status: Literal["graph", "quarantine"]
    graph: ComplaintGraph | None
    quarantine_record: dict[str, Any] | None


def extract_graph(
    review: RawReview,
    response_text: str,
) -> L2Outcome:
    """Turn a Claude response into an :class:`L2Outcome`.

    The pure pipeline (no I/O). Split from :func:`structure_one` so
    tests can exercise parsing + post-processing without a Claude
    client or replay log. Also lets the CLI's ``--retry-quarantine``
    path (if we ever add one) re-run extraction on a cached raw
    response.

    Routing:

    1. Parse response → raw nodes + edges. A :class:`ParseError`
       propagates to the caller (structure_one / structure_batch).
    2. Compute offsets. Any missing verbatim → ``hallucination``
       quarantine.
    3. Run substring-containment check. A hit → ``substring_containment``
       quarantine.
    4. Check node count. <3 → ``under_minimum_nodes`` quarantine;
       >7 → ``over_maximum_nodes`` quarantine (should never happen
       if the model obeys; routing it beats crashing on Pydantic).
    5. Build :class:`ComplaintGraph`; Pydantic re-validates (defence
       in depth) → ``schema_violation`` on the off chance we missed
       something.
    6. Run :func:`validate_complaint_graph_against_source` — redundant
       with step 2 but belt-and-suspenders (§4.3 P1).
    """
    nodes, edges = parse_response(response_text)

    # Step 2: compute offsets BEFORE counting, so that a hallucinated
    # node doesn't inflate the node count past MAX_NODES.
    nodes_with_offsets, missing = compute_node_offsets(nodes, review.text)
    if missing:
        return _quarantine(
            review_id=review.review_id,
            reason="hallucination",
            node_count=len(nodes),
            detail=(
                f"verbatim_quote not found in source for node_ids={missing}"
            ),
        )

    # Step 3: substring-containment padding (skill Fix C). Run on
    # nodes that passed step 2 — missing-quote nodes are already gone.
    padding_hit, padding_detail = has_substring_containment(nodes_with_offsets)
    if padding_hit:
        return _quarantine(
            review_id=review.review_id,
            reason="substring_containment",
            node_count=len(nodes_with_offsets),
            detail=padding_detail,
        )

    # Step 4: node count gates.
    n = len(nodes_with_offsets)
    if n < MIN_NODES:
        return _quarantine(
            review_id=review.review_id,
            reason="under_minimum_nodes",
            node_count=n,
            detail=f"{n} nodes < MIN_NODES={MIN_NODES} (thin-review path, SKILL §thin)",
        )
    if n > MAX_NODES:
        return _quarantine(
            review_id=review.review_id,
            reason="over_maximum_nodes",
            node_count=n,
            detail=f"{n} nodes > MAX_NODES={MAX_NODES} (should not happen — model drift?)",
        )

    # Step 5: build the Pydantic graph. Duplicate IDs / bad edges
    # already caught in parse_response, so this is belt-and-suspenders.
    try:
        graph = ComplaintGraph(
            review_id=review.review_id,
            nodes=[ComplaintNode(**nd) for nd in nodes_with_offsets],
            edges=[ComplaintEdge(**ed) for ed in edges],
        )
    except ValidationError as e:
        return _quarantine(
            review_id=review.review_id,
            reason="schema_violation",
            node_count=n,
            detail=f"ComplaintGraph construction failed: {e}",
        )

    # Step 6: source-anchored check. Redundant with step 2 but cheap
    # and the canonical audit-trail invariant (§4.3 P1).
    try:
        validate_complaint_graph_against_source(graph, source_text=review.text)
    except SchemaValidationError as e:
        return _quarantine(
            review_id=review.review_id,
            reason="hallucination",
            node_count=n,
            detail=f"source validation failed: {e}",
        )

    return L2Outcome(
        review_id=review.review_id,
        status="graph",
        graph=graph,
        quarantine_record=None,
    )


def _quarantine(
    *,
    review_id: str,
    reason: QuarantineReason,
    node_count: int,
    detail: str,
) -> L2Outcome:
    """Build a quarantine-status :class:`L2Outcome`.

    Centralised so the record shape is consistent across reasons —
    L3 and audit tooling can assume these five keys exist on every
    quarantine row.
    """
    return L2Outcome(
        review_id=review_id,
        status="quarantine",
        graph=None,
        quarantine_record={
            "review_id": review_id,
            "reason": reason,
            "node_count": node_count,
            "detail": detail,
            "processed_at": datetime.now(UTC).isoformat(),
        },
    )


# ---------------------------------------------------------------------------
# Claude call + batching
# ---------------------------------------------------------------------------


async def structure_one(
    review: RawReview,
    client: Client,
    *,
    model: str = MODEL,
    skill_id: str = SKILL_ID,
    skill_hash_value: str,
) -> L2Outcome:
    """Structure one review. Raises :class:`ParseError` on bad response.

    Quarantine paths (hallucination / padding / under-minimum / schema)
    are NOT raised — they are routed to :class:`L2Outcome` with
    ``status="quarantine"``. Only genuine parse/vocab failures
    propagate up to the batch.
    """
    user = build_user_message(review)
    resp = await client.call(
        system=SYSTEM_PROMPT,
        user=user,
        model=model,
        skill_id=skill_id,
        skill_hash=skill_hash_value,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )
    return extract_graph(review, resp.response)


async def structure_batch(
    reviews: list[RawReview],
    client: Client,
    *,
    model: str = MODEL,
    skill_id: str = SKILL_ID,
    skill_hash_value: str | None = None,
) -> tuple[
    list[ComplaintGraph],
    list[dict[str, Any]],
    list[tuple[str, Exception]],
]:
    """Structure a list of reviews concurrently.

    Returns:
        ``(graphs, quarantine_records, failures)`` — graphs go to
        ``data/derived/l2_graphs.jsonl``, quarantine_records go to
        ``data/quarantine/l2_thin.jsonl``, failures are logged and
        reported to the operator (not persisted).
    """
    sh = skill_hash_value if skill_hash_value is not None else skill_hash()

    async def _one(r: RawReview) -> tuple[str, L2Outcome | Exception]:
        try:
            outcome = await structure_one(
                r,
                client,
                model=model,
                skill_id=skill_id,
                skill_hash_value=sh,
            )
            return (r.review_id, outcome)
        except Exception as e:  # noqa: BLE001 — per-review isolation
            return (r.review_id, e)

    results = await asyncio.gather(*(_one(r) for r in reviews))
    graphs: list[ComplaintGraph] = []
    quarantines: list[dict[str, Any]] = []
    failures: list[tuple[str, Exception]] = []
    for rid, payload in results:
        if isinstance(payload, L2Outcome):
            if payload.status == "graph":
                assert payload.graph is not None
                graphs.append(payload.graph)
            else:
                assert payload.quarantine_record is not None
                quarantines.append(payload.quarantine_record)
        else:
            failures.append((rid, payload))
    return graphs, quarantines, failures


# ---------------------------------------------------------------------------
# IO — corpus filtering + idempotent rerun
# ---------------------------------------------------------------------------


def load_ux_relevant_reviews(
    corpus_path: Path,
    classified_path: Path,
) -> list[RawReview]:
    """Load corpus filtered to reviews classified UX-relevant by L1.

    Reads the full corpus + the L1 output, returns only the reviews
    whose ``review_id`` appears with ``is_ux_relevant=True`` in the
    L1 classifications. Preserves corpus order (sorted by review_id
    downstream via :func:`structure_batch`'s merge).

    Raises:
        ValueError: If the corpus is malformed, or if the L1
            classification file is missing entirely (L2 cannot run
            without L1 having processed the corpus — fail loudly).
    """
    if not classified_path.exists():
        raise ValueError(
            f"L1 classification file not found at {classified_path} — "
            "run l1_classify first"
        )

    ux_ids: set[str] = set()
    for i, raw in enumerate(read_jsonl(classified_path), start=1):
        try:
            rec = ClassifiedReview.model_validate(raw)
        except ValidationError as e:
            raise ValueError(f"{classified_path}: line {i}: {e}") from e
        if rec.is_ux_relevant:
            ux_ids.add(rec.review_id)

    kept: list[RawReview] = []
    total = 0
    for i, raw in enumerate(read_jsonl(corpus_path), start=1):
        total += 1
        try:
            review = RawReview.model_validate(raw)
        except ValidationError as e:
            raise ValueError(f"{corpus_path}: line {i}: {e}") from e
        if review.review_id in ux_ids:
            kept.append(review)

    _log.info(
        "corpus=%d classifications=%d ux_relevant=%d",
        total,
        len(ux_ids),
        len(kept),
    )
    return kept


def load_existing_graphs(path: Path) -> list[ComplaintGraph]:
    """Read prior graph output. Empty list if missing or every row invalid.

    Invalid lines are dropped with a warning (not raised) — a partial
    or drifted prior output should not block a rerun.
    """
    if not path.exists():
        return []
    graphs: list[ComplaintGraph] = []
    for i, raw in enumerate(read_jsonl(path), start=1):
        try:
            graphs.append(ComplaintGraph.model_validate(raw))
        except ValidationError as e:
            _log.warning("%s: line %d invalid, dropping: %s", path, i, e)
    return graphs


def load_existing_thin(path: Path) -> list[dict[str, Any]]:
    """Read prior quarantine output. Empty list if missing.

    Quarantine rows are plain dicts (no dedicated Pydantic model — the
    shape is internal pipeline telemetry, not a consumed artifact).
    Rows missing the canonical five keys are dropped with a warning.
    """
    if not path.exists():
        return []
    required = {"review_id", "reason", "node_count", "detail", "processed_at"}
    records: list[dict[str, Any]] = []
    for i, raw in enumerate(read_jsonl(path), start=1):
        if not required.issubset(raw.keys()):
            _log.warning(
                "%s: line %d missing required keys %s, dropping",
                path,
                i,
                sorted(required - set(raw.keys())),
            )
            continue
        records.append(raw)
    return records


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _default_run_id() -> str:
    return f"l2-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"


def main(argv: list[str] | None = None) -> int:
    repo_root = _resolve_repo_root()

    parser = argparse.ArgumentParser(
        description="L2 structure — per-review complaint graph extraction.",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=repo_root / DEFAULT_CORPUS,
        help=f"Raw corpus JSONL (default: {DEFAULT_CORPUS}).",
    )
    parser.add_argument(
        "--classified",
        type=Path,
        default=repo_root / DEFAULT_CLASSIFIED,
        help=f"L1 classification JSONL (default: {DEFAULT_CLASSIFIED}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / DEFAULT_GRAPHS,
        help=f"Graph output JSONL (default: {DEFAULT_GRAPHS}).",
    )
    parser.add_argument(
        "--quarantine",
        type=Path,
        default=repo_root / DEFAULT_QUARANTINE,
        help=f"Quarantine output JSONL (default: {DEFAULT_QUARANTINE}).",
    )
    parser.add_argument(
        "--mode",
        choices=("live", "replay"),
        default="replay",
        help="Claude client mode (default: replay — reviewer-safe).",
    )
    parser.add_argument("--model", default=MODEL, help=f"Claude model (default: {MODEL}).")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max reviews after filtering (head-select by review_id).",
    )
    parser.add_argument(
        "--replay-log",
        type=Path,
        default=repo_root / "data/cache/responses.jsonl",
        help="Path to the Claude replay log (default: data/cache/responses.jsonl).",
    )
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument(
        "--usd-ceiling",
        type=float,
        default=10.0,
        help="Per-run USD kill-switch ceiling (live mode only).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional run_id; default is 'l2-YYYYmmddTHHMMSS' at UTC now.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    # Filter corpus → UX-relevant only.
    reviews = load_ux_relevant_reviews(args.corpus, args.classified)

    if args.limit is not None:
        reviews = sorted(reviews, key=lambda r: r.review_id)[: args.limit]
        _log.info("head-limited to %d reviews", len(reviews))

    # Idempotency: skip reviews already in either output file.
    existing_graphs = load_existing_graphs(args.output)
    existing_thin = load_existing_thin(args.quarantine)
    processed_ids = (
        {g.review_id for g in existing_graphs}
        | {t["review_id"] for t in existing_thin}
    )
    new_reviews = [r for r in reviews if r.review_id not in processed_ids]
    if processed_ids:
        _log.info(
            "%d/%d reviews already processed (%d graphs + %d thin) — %d new to structure",
            len(processed_ids & {r.review_id for r in reviews}),
            len(reviews),
            len(existing_graphs),
            len(existing_thin),
            len(new_reviews),
        )

    run_id = args.run_id or _default_run_id()

    client = Client(
        mode=args.mode,
        run_id=run_id,
        replay_log_path=args.replay_log,
        usd_ceiling=args.usd_ceiling,
        concurrency=args.concurrency,
    )
    _log.info(
        "client mode=%s replay-log=%s cache_size=%d usd_ceiling=$%.2f",
        args.mode,
        args.replay_log,
        client.cache_size,
        args.usd_ceiling,
    )

    if new_reviews:
        new_graphs, new_thin, failures = asyncio.run(
            structure_batch(
                new_reviews,
                client,
                model=args.model,
            )
        )
    else:
        new_graphs, new_thin, failures = [], [], []
        _log.info("nothing new to structure — rerun is a no-op")

    if failures:
        for rid, err in failures:
            _log.warning("structure failed for %s: %s: %s", rid[:8], type(err).__name__, err)
        _log.error("%d/%d structurings failed (parse/LLM)", len(failures), len(new_reviews))

    # Merge + dedup.
    graphs_by_id: dict[str, ComplaintGraph] = {g.review_id: g for g in existing_graphs}
    for g in new_graphs:
        graphs_by_id[g.review_id] = g
    combined_graphs = sorted(graphs_by_id.values(), key=lambda g: g.review_id)

    thin_by_id: dict[str, dict[str, Any]] = {t["review_id"]: t for t in existing_thin}
    for t in new_thin:
        thin_by_id[t["review_id"]] = t
    combined_thin = sorted(thin_by_id.values(), key=lambda t: t["review_id"])

    # Ensure parent dirs exist (storage.write_jsonl_atomic does not mkdir).
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.quarantine.parent.mkdir(parents=True, exist_ok=True)

    corpus_hash = hash_file(args.corpus)
    classified_hash = hash_file(args.classified)

    graph_meta = write_jsonl_atomic(
        args.output,
        [g.model_dump(mode="json") for g in combined_graphs],
        run_id=run_id,
        layer=LAYER_NAME,
        input_hashes={
            args.corpus.name: corpus_hash,
            args.classified.name: classified_hash,
        },
        skill_hashes={SKILL_ID: skill_hash()},
        schema_version=SCHEMA_VERSION,
        repo_root=repo_root,
    )
    _log.info(
        "wrote %d graphs to %s (sha256=%s…)",
        len(combined_graphs),
        args.output,
        graph_meta.artifact_sha256[:16],
    )

    thin_meta = write_jsonl_atomic(
        args.quarantine,
        combined_thin,
        run_id=run_id,
        layer=LAYER_NAME,
        input_hashes={
            args.corpus.name: corpus_hash,
            args.classified.name: classified_hash,
        },
        skill_hashes={SKILL_ID: skill_hash()},
        schema_version=SCHEMA_VERSION,
        repo_root=repo_root,
    )
    _log.info(
        "wrote %d quarantine rows to %s (sha256=%s…)",
        len(combined_thin),
        args.quarantine,
        thin_meta.artifact_sha256[:16],
    )

    # Quarantine reason histogram — cheap signal on pilot health.
    if combined_thin:
        from collections import Counter

        counts = Counter(t["reason"] for t in combined_thin)
        _log.info("quarantine reasons: %s", dict(counts.most_common()))

    _log.info(
        "L2 structure done. mode=%s live-spend=$%.4f graphs=%d thin=%d failures=%d",
        args.mode,
        client.cumulative_usd,
        len(combined_graphs),
        len(combined_thin),
        len(failures),
    )

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
