# Auditable Design — Technical Architecture

**Companion to `concept.md` — describes how the ten conceptual layers physically compose into a running application.**

**Author:** Szymon P. Pepliński
**Hackathon:** Built with Opus 4.7, 21–26 April 2026
**Status:** implementation contract; deviations must be reflected here and in `concept.md`
**Normative ADRs:** `docs/ADRs.md` — architectural decisions cross-referenced as ADR-001 … ADR-012; any conflict between this document and an Accepted ADR is resolved in favor of the ADR

---

## 0. Reading guide

This document is not a re-statement of `concept.md`. Reading order:

1. `concept.md` — what the system is and why
2. `ARCHITECTURE.md` (this file) — how it is physically built
3. `IMPLEMENTATION_PLAN.md` — when each piece is built during the hackathon

Every section of this document maps to one or more layers defined in `concept.md` sections 4–13. The mapping is declared explicitly at the head of each section.

---

## 1. Design principles

The architecture obeys seven constraints that flow directly from the concept and the ADRs:

**P1 — Append-only provenance.** Every artifact (review, mini-graph, cluster, verdict, decision, iteration) carries a stable ID and an immutable trace back to the artifacts that produced it. Traceability is not a reporting feature; it is a storage invariant.

**P2 — Skills as pure functions.** Each Claude skill consumes a typed input and returns a typed output with no side effects on other components. Reconfigurability follows from this: swapping a canonical skill (Norman → WCAG) cannot break layers upstream or downstream.

**P3 — Uniform verdict contract, mechanically enforced.** All six canonical audit skills emit a structured JSON tail matching a published JSONSchema (ADR-002). The SDK-level `response_format` is the primary guarantee; Pydantic validation at the client is the belt-and-suspenders check, with one schema-repair retry before the record is quarantined.

**P4 — Deterministic orchestration, non-deterministic tools, replayable by default.** The pipeline that wires Claude calls is deterministic, idempotent, and checkpointed. Reproducibility is bound to an append-only replay log with a SHA-256 manifest (ADR-007, ADR-011); `RunContext.client_mode = "replay"` forbids outbound calls entirely.

**P5 — Build the spine first.** A thin end-to-end path (10 reviews → 1 cluster → 1 verdict → 1 decision → 1 iteration → 1 evolution graph node) must exist by end of Day 2. Everything else widens the spine.

**P6 — Adversarial-input discipline.** User-generated text (Google Play reviews) is never interpolated raw into prompts. All user text flows through `wrap_user_text()` which tags it `<user_review id=...>...</user_review>` and the system prompt declares those tags as data-not-instructions (ADR-010). Output fields that propagate to the demo are screened for directive-shaped tokens before bundling.

**P7 — Atomic writes + hashed re-entry.** Layer outputs are written via `write_jsonl_atomic` (tmp → fsync → rename), paired with a sidecar `{output}.meta.json` carrying run context and the full-directory hash of every skill consumed by the layer (ADR-003). Re-entry compares sidecars; hash drift forces re-run even if the output file exists.

---

## 2. Component map

```
┌─────────────────────────────────────────────────────────────────────────┐
│                             DEMO SURFACE                                │
│   React SPA (Vite) ─── Tailwind + shadcn/ui ─── D3 (evolution graph)    │
└─────────────────────────────────────────────────────────────────────────┘
                                    ▲
                                    │ static JSON bundles + served assets
                                    │
┌─────────────────────────────────────────────────────────────────────────┐
│                          ORCHESTRATION LAYER                            │
│                                                                         │
│   pipeline.py  ──  typed DAG runner (layers 1–10)                       │
│   ├── layer modules (one file per layer, pure functions over stores)    │
│   ├── claude_client.py  (Anthropic SDK wrapper + retries + caching)     │
│   ├── skills/  (SKILL.md assets invoked by claude_client)               │
│   └── run_context.py  (seed, config, meta-weights, run_id)              │
└─────────────────────────────────────────────────────────────────────────┘
                                    ▲
                                    │
┌─────────────────────────────────────────────────────────────────────────┐
│                          STORAGE LAYER                                  │
│                                                                         │
│   data/raw/          Duolingo review corpus (CSV/JSONL, immutable)      │
│   data/derived/      layer outputs (JSONL per layer, keyed by run_id)   │
│   data/log/          append-only optimization log (JSONL)               │
│   data/artifacts/    final spec, prototype bundle, evolution graph      │
│   data/cache/        Claude response cache (sha256(prompt)→response)    │
└─────────────────────────────────────────────────────────────────────────┘
```

All persistence is file-backed. No database, no server process; the demo is a static bundle with a tiny Flask/FastAPI dev server only if interactive meta-weight sliders (§7) require live recomputation.

---

## 3. Repository layout

```
hackathon-opus-47/
├── concept.md                      # source of truth (exists)
├── ARCHITECTURE.md                 # this document
├── IMPLEMENTATION_PLAN.md          # day-by-day schedule
├── README.md                       # pitch-facing
├── CONTEXT_DUOLINGO.md             # public context doc (§9 of concept)
│
├── pyproject.toml                  # Python 3.11, uv-managed
├── package.json                    # demo SPA (Vite + React)
│
├── src/auditable_design/           # the Python package (installed by `uv sync`)
│   ├── __init__.py
│   ├── cli.py                      # typer entry point (`auditable …`)
│   ├── pipeline.py                 # DAG runner
│   ├── run_context.py              # run config + meta-weights + client_mode
│   ├── claude_client.py            # live/replay dual-mode client (ADR-002/005/010/011)
│   ├── schemas.py                  # Pydantic models for every artifact
│   ├── storage.py                  # atomic writes, sidecar hashing (ADR-003)
│   ├── logging_setup.py            # JSON structured logs (ADR-012)
│   ├── pricing.py                  # per-model token cost constants
│   ├── prompt_builder.py           # wraps user text in <user_review> tags (ADR-010)
│   │
│   ├── layers/
│   │   ├── __init__.py
│   │   ├── l1_classify.py          # UX-relevance filter
│   │   ├── l2_structure.py         # structure-of-complaint invocation
│   │   ├── l3_cluster.py           # embedding + HDBSCAN
│   │   ├── l3b_label.py            # cluster labeling via Claude (concept §6 extension, see §4.4)
│   │   ├── l4_audit.py             # parallel canonical skill runner
│   │   ├── l5_reconcile.py         # SOT meta-reconciliation
│   │   ├── l6_weight.py            # 5-dim business scoring
│   │   ├── l7_decide.py            # principle → decision generation
│   │   ├── l8_optimize.py          # optimization loop + pareto check
│   │   ├── l9_render.py            # final spec + prototype export
│   │   └── l10_evolution.py        # graph assembly for demo
│   │
│   ├── embedders/
│   │   ├── __init__.py
│   │   └── local_encoder.py        # sentence-transformers wrapper
│   │
│   └── evaluators/
│       ├── __init__.py
│       └── pareto.py               # dominance + weighted-sum fallback
│
├── skills/                         # all skills authored in the hackathon
│   ├── structure-of-complaint/     # Layer 2 (new, concept §5)
│   ├── sot-reconcile/              # Layer 5 (new, concept §8)
│   ├── audit-usability-fundamentals/   # Norman
│   ├── audit-interaction-design/       # Cooper
│   ├── audit-ux-architecture/          # Garrett
│   ├── audit-decision-psychology/      # Kahneman
│   ├── audit-business-alignment/       # Osterwalder
│   └── audit-accessibility/            # WCAG 2.2 + Inclusive Design (new)
│
├── demo/                           # React SPA
│   ├── index.html
│   ├── vite.config.ts
│   └── src/
│       ├── App.tsx
│       ├── views/
│       │   ├── TimelineView.tsx        # Layer 10 View A
│       │   ├── RationaleView.tsx       # Layer 10 View B
│       │   ├── CompareView.tsx         # baseline B1/B2/B3
│       │   └── MetaWeightsPanel.tsx    # Layer 6 sliders
│       ├── components/                 # shadcn/ui
│       └── graph/                      # D3 renderers
│
├── data/
│   ├── raw/corpus.jsonl                # frozen corpus (600 items, CONTEXT §2)
│   ├── derived/                        # per-layer outputs
│   ├── log/optimization.jsonl          # append-only
│   ├── artifacts/                      # final deliverables
│   └── cache/                          # Claude response cache
│
├── scripts/
│   ├── collect_reviews.py          # Google Play scraping (reproducible)
│   ├── sample_spotcheck.py         # 20-review sanity sample runner
│   ├── build_demo_bundle.py        # freezes JSON for static demo
│   ├── check_budget.py             # pre-flight cost gate (§5.5, ADR-015)
│   ├── generate_replay_manifest.py # sha256 per cache entry + tree hash (§10, ADR-011)
│   ├── verify_replay_manifest.py   # reverse of above, runs in CI (pages.yml)
│   ├── log_summary.py              # per-run cost/latency breakdown (§10.1)
│   └── verify_sources.py           # checks CONTEXT_DUOLINGO footnotes (§9)
│
│   # Note: uv.lock regeneration is handled by `.github/workflows/relock.yml`
│   # (workflow_dispatch → PR). Operator action, not a build step — see ADR-013.
│
└── tests/
    ├── fixtures/                   # 20-review micro-corpus
    └── test_*.py                   # smoke tests per layer
```

**Invariant:** every file under `src/auditable_design/layers/` exposes the same contract — `run(run_ctx) -> None`, reads from `data/derived/l{n-1}/*.jsonl`, writes to `data/derived/l{n}/*.jsonl`. This lets the DAG runner remain trivially simple (~40 LOC) and lets any layer be re-executed in isolation.

---

## 4. Data model

All artifacts are Pydantic models (authoritative definitions in `src/schemas.py`) serialized as JSONL. IDs are deterministic hashes of canonical inputs, so re-running a layer with identical inputs produces identical IDs — enabling caching and re-entry.

### 4.1 Raw corpus

```python
class RawReview(BaseModel):
    review_id: str              # sha1(source + author_hash + timestamp)
    source: Literal["google_play"]
    author_hash: str            # hashed, never raw name
    timestamp_utc: datetime
    rating: int                 # 1–5
    text: str                   # verbatim
    lang: str                   # "en" expected; other langs filtered
    app_version: Optional[str]
```

### 4.2 Layer 1 — classification

```python
class ClassifiedReview(BaseModel):
    review_id: str              # FK → RawReview
    is_ux_relevant: bool
    classifier_confidence: float
    rubric_tags: list[str]      # {interface, layout, paywall, ...} or {billing, content, ...}
    classified_at: datetime
```

### 4.3 Layer 2 — structure-of-complaint mini-graph

```python
class ComplaintNode(BaseModel):
    node_id: str
    node_type: Literal["pain","expectation","triggered_element","workaround","lost_value"]
    verbatim_quote: str         # MUST be substring of source review (enforced)
    quote_start: int            # char offset in source text
    quote_end: int

class ComplaintEdge(BaseModel):
    src: str
    dst: str
    relation: Literal["triggers","violates_expectation","compensates_for","correlates_with"]

class ComplaintGraph(BaseModel):
    review_id: str              # FK
    nodes: list[ComplaintNode]  # 3–7 nodes
    edges: list[ComplaintEdge]
```

**Hallucination safeguard (P1):** `verbatim_quote` is validated at ingest — any node whose quote is not a substring of the source review is rejected, and the review is flagged for re-processing.

### 4.4 Layer 3 — insight clusters

```python
class InsightCluster(BaseModel):
    cluster_id: str
    label: str                  # "UNLABELED:cluster_NN" from L3; rewritten by labeling pass
    member_review_ids: list[str]        # multi-membership allowed (see below)
    centroid_vector_ref: str            # "<file>#<index>" pointer into sibling .npy
    representative_quotes: list[str]    # top-5 closest to centroid
```

**L3 artifact layout.** L3 writes four files per run:

- `l3_clusters.jsonl` — one `InsightCluster` per line
- `l3_clusters.jsonl.meta.json` — standard `ArtifactMeta` sidecar (ADR-011); `input_hashes` covers both the L2 graphs file *and* `l3_centroids.npy`, so a reader can detect drift in either upstream
- `l3_centroids.npy` — stacked `(n_clusters, embedding_dim)` float32 array. Row `i` holds the centroid for `cluster_id == i` — see "Label normalization" below
- `l3_clusters.provenance.json` — encoder + clustering runtime tuple (model weights hash, torch/sentence-transformers/hdbscan/sklearn versions, platform). Separate from `ArtifactMeta` because runtime fingerprinting is L3-specific; extending `ArtifactMeta` would pollute every layer. **Auditor-facing and informational only** — its contents are not hashed into the replay chain (ADR-011 verifies `jsonl + meta + npy` drift, not `provenance.json` drift). A reviewer consults it to explain *why* two replays diverge; they do not use it to decide *whether* they diverge.

**`centroid_vector_ref` format.** `"<file>#<index>"` where `<file>` is the basename of the sibling `.npy` and `<index>` is the row. Resolution: `np.load(dir / file)[int(index)]`. Kept as a string (not a path + int pair) so it round-trips through JSON without special-casing.

**Label normalization.** L3 remaps clustering-backend labels to a contiguous `0..k-1` range before writing (noise `-1` is preserved but not stored). This makes `cluster_id == row index in l3_centroids.npy` true **by construction** — the pointer is correct regardless of which backend produced the labels or whether that backend happens to use contiguous labels natively. See `_normalize_labels` in `src/auditable_design/layers/l3_cluster.py`.

**Multi-membership.** A `review_id` may appear in `member_review_ids` of multiple clusters — this is expected whenever a review's `pain` and `expectation` nodes land in different clusters. L4/L5 aggregate per-cluster; they do not assume reviews are partitioned.

**Label lifecycle and L3b.** L3 produces `"UNLABELED:cluster_NN"` placeholder labels and writes no `skill_hashes` (no Claude call). Human-readable labels are produced by a distinct downstream layer — **L3b** (`l3b_label`) — which:

- Reads `l3_clusters.jsonl` *only* — `cluster_id` + `representative_quotes` are the sole inputs the labeling skill needs. The three sibling files (`l3_clusters.jsonl.meta.json`, `l3_centroids.npy`, `l3_clusters.provenance.json`) are informational context for a human reviewer, **not** inputs to L3b; they do not appear in L3b's `input_hashes`. Keeping the input contract to a single file means an unrelated bump of torch/hdbscan/sklearn versions (visible only in `provenance.json`) does not invalidate L3b's replay chain.
- Calls the labeling skill for each cluster's `representative_quotes`
- Writes `l3b_labeled_clusters.jsonl` (+ standard sidecar, with its own non-empty `skill_hashes`)
- Leaves the L3 artifact untouched — ADR-011 immutability invariant is preserved

The "L3b" name is chosen over "L4" (would cascade-renumber L4–L10) or "L3.5" (violates integer layer convention). `cluster_id` is stable across L3 and L3b; only the `label` field is rewritten.

**Empirical note — N=50 run.** On the same 50-review corpus, Opus 4.6's L2 output (73 clusterable pain/expectation nodes) lets HDBSCAN find 2 dense clusters with `fallback_reason: null`; Opus 4.7's L2 output (75 clusterable nodes) hits `0 valid clusters` at `min_cluster_size=5` and routes to KMeans fallback (`kmeans_k=6`). Identical encoder (MiniLM, `model_weights_hash=15de32948ddab731`), identical seed, identical params. The cluster-quality ordering is the reverse of what the exit status suggests: Opus 4.6's `cluster_00` bundles `"Worst app ever"` and `"used to love this app"` — opposite sentiments with near-identical MiniLM embeddings because both are high-intensity meta-level assertions — while Opus 4.7's six fallback-produced clusters map to distinguishable themes (accuracy complaints, performance, sentiment/nostalgia, learning-mode expectations, generic negativity, one noisy multilingual bucket). This is the failure mode the audit story is for: `fallback_reason: null` is not a green light; the `representative_quotes` are. Both `provenance.json` sidecars carry enough detail — encoder hash, clustering algorithm, fallback reason — that a reviewer replaying either run can reconstruct the divergence without re-executing the pipeline.

Caveats on the comparison rather than in a separate evaluation file: (a) KMeans always produces exactly `k` clusters, so the 6-cluster outcome on Opus 4.7 is partly a function of the `kmeans_k` parameter — a silhouette sweep across `k ∈ {3..8}` would distinguish data-driven from parameter-driven cluster counts; (b) MiniLM is English-centric, which surfaces on the Opus 4.7 output as a noisy cluster that collapses Indonesian and English quotes into one bucket — a multilingual encoder (e.g. `paraphrase-multilingual-MiniLM-L12-v2`) or an upstream language filter would fix this; (c) N=73–75 is too small for silhouette or adjusted-Rand scores to be informative, so the comparison above is interpretive, not statistical. None of these block L3 from functioning; they are notes for when corpus size grows or when the language-filter gap is closed upstream.

**Full-corpus update.** The N=50 observations above are superseded by three full-corpus runs on each model's own L2 pain+expectation pool (`docs/evals/l3_full_corpus_three_way.md`). All three resolve to HDBSCAN primary output with no KMeans fallback: 14 / 10 / 7 clusters on 621 / 528 / 480 input nodes for opus46 / opus47 / sonnet46 respectively. Caveat (a) is moot at scale — KMeans is not invoked. Caveat (c) is resolved — N ≥ 480 is large enough for density-based structure to emerge and the tentpole "used to love this app" regression cluster recovers identically across all three models. Caveat (b) remains partially valid: MiniLM is English-centric and Spanish/Portuguese regret quotes cluster together in both Opus runs (cluster_00 of each) without semantic alignment to their English equivalents. The full-corpus eval also documents per-model unique-theme findings (opus47 "freezing"; sonnet46 "chess"; opus46 affect atomisation) and a junk-drawer cluster in sonnet46 that motivates an L4 cluster-coherence audit. The N=50 principle — *`fallback_reason: null` is not a green light; the representative_quotes are* — still applies; full-corpus just supplies enough density for HDBSCAN to find real structure rather than collapse to high-intensity token clusters.

### 4.5 Layer 4 — audit verdicts (uniform contract, P3; severity anchors per ADR-008)

```python
class HeuristicViolation(BaseModel):
    heuristic: str
    violation: str
    severity: int               # 0–10, anchored per skill in skills/<name>/rubric.md
    evidence_review_ids: list[str]
    reasoning: str

class AuditVerdict(BaseModel):
    verdict_id: str
    cluster_id: str             # FK
    skill_id: str               # e.g. "audit-usability-fundamentals"
    relevant_heuristics: list[HeuristicViolation]
    native_payload_ref: Optional[str]   # path to full skill narrative (e.g. Norman scorecard)
    produced_at: datetime
    claude_model: str           # frozen per run for reproducibility
    skill_hash: str             # sha256 of the skill directory at call time
```

**Severity anchoring (ADR-008):** 0 = non-issue, 3 = cosmetic, 6 = material, 9 = critical; intermediate values interpolated. Each skill's `rubric.md` translates these anchors into that skill's native vocabulary. Cross-skill dominance in L8 is applied **per-skill-vector** (ADR-008, ARCHITECTURE §7.2) — a candidate dominates iff it dominates within every active skill — not on a flat concatenation of all heuristics.

### 4.6 Layer 5 — reconciled verdicts

```python
class ReconciledVerdict(BaseModel):
    cluster_id: str
    ranked_violations: list[HeuristicViolation]  # cross-skill ranked
    tensions: list[dict]        # e.g. {"skill_a":"cooper","skill_b":"norman","axis":"user_control","resolution":...}
```

### 4.7 Layer 6 — weighted priority

```python
class PriorityScore(BaseModel):
    cluster_id: str
    dimensions: dict[str, int]  # 5 dims, each 0–10
    meta_weights: dict[str, float]       # editable via UI
    weighted_total: float
    validation_passes: int      # 2 or 3
    validation_delta: float     # max diff between passes
```

### 4.8 Layers 7–9 — decisions, iterations, final

```python
class DesignPrinciple(BaseModel):
    principle_id: str
    cluster_id: str             # FK
    name: str                   # short memorable
    statement: str              # constraining, traceable, operational
    derived_from_review_ids: list[str]

class DesignDecision(BaseModel):
    decision_id: str
    principle_id: str           # FK
    description: str            # specific, actionable
    before_snapshot: str        # ref
    after_snapshot: str         # ref
    resolves_heuristics: list[str]   # REQUIRED — empty list rejected

class OptimizationIteration(BaseModel):
    iteration_id: str
    run_id: str
    iteration_index: int        # 0 = initial
    parent_iteration_id: Optional[str]
    design_artifact_ref: str    # path in data/artifacts/iterations/
    scores: dict[str, dict[str, int]]   # {skill_id: {heuristic: severity}}
    reasoning: str
    accepted: bool
    regression_reason: Optional[str]
    delta_per_heuristic: dict[str, int]
    informing_review_ids: list[str]
    recorded_at: datetime
```

### 4.9 Evolution graph

```python
class EvolutionNode(BaseModel):
    node_id: str
    kind: Literal["review","cluster","verdict","decision","iteration","element"]
    payload_ref: str

class EvolutionEdge(BaseModel):
    src: str
    dst: str
    relation: Literal["informs","audited_by","reconciled_into","prioritized_as",
                      "decided_as","iterated_to","produced_element","dismissed_for"]
```

---

## 5. Orchestration

### 5.1 DAG runner (`pipeline.py`)

A layer is a function `run(ctx: RunContext) -> None`. The runner walks an explicit DAG:

```
L1 → L2 → L3 → L4 → L5 → L6 → L7 → L8 → L9 → L10
                    (L4 fanned out per {cluster × skill} — embarrassingly parallel)
```

Re-entry is file-based: if `data/derived/l4/{run_id}.jsonl` exists, L4 is skipped unless `--force l4` is passed. This lets the author iterate on L7–L10 without re-spending Claude budget on L1–L6.

### 5.2 Claude client (`claude_client.py`)

One wrapper around the Anthropic SDK. Dual-mode (ADR-011): `"live"` hits the API and appends to the replay log; `"replay"` serves only from the replay log and raises on miss. Both modes share the same hashing and validation pipeline.

Responsibilities:

- **Attach skill assets (layer-side).** The calling layer loads `SKILL.md` + any `references/*.md` from the named skill directory and assembles the system prompt. The client receives pre-assembled `system` and `user` strings — it does not read skill files itself. This keeps `claude_client` free of filesystem concerns beyond the replay log.
- **Wrap user text (ADR-010, P6; layer-side).** Every user-supplied string is wrapped by the calling layer through `prompt_builder.wrap_user_text(id, text)`, which produces `<user_review id="...">{text}</user_review>`; the system prompt (assembled by the layer) declares those tags data-not-instructions. The client has no visibility into `review_id`, so wrapping necessarily lives one level up.
- **Structured output (ADR-002).** Load `skills/<name>/schema.json`, pass as `response_format={"type":"json_schema", "json_schema":...}`. Pydantic-validate the returned tail; on failure, one repair-retry with the validation error appended; on second failure, quarantine the record to `data/quarantine/{call_id}.json` and surface in the pipeline log.
- **Replay log.** Append-only `data/cache/responses.jsonl`, one record per call:
  `{call_id, key_hash, skill_id, skill_hash, model, temperature, prompt, response, input_tokens, output_tokens, cost_usd, timestamp}`. The `prompt` field is the concatenation of system and user rendered by `_canonical_prompt(system, user)` (tab-delimited `SYSTEM:\t{system}\tUSER:\t{user}`). `cost_usd` is computed from `model + input_tokens + output_tokens` by `estimate_cost_usd(...)` so that the manifest verifier (§10, ADR-011) can be kept single-file and does not need to reproduce the pricing table. Key hash canonicalisation is described in §10.
- **Request coalescing (ADR-005).** In-memory `dict[key_hash, asyncio.Future]`; concurrent callers with matching keys await the same future instead of duplicating the API call.
- **Rate limiting.** Token-bucket limiter per model; configurable RPM. Env var `MAX_CLAUDE_SPEND_USD` halts the pipeline with a clear error message if exceeded.
- **Exponential backoff** on 429/5xx with jitter.
- **Per-call logging** via `logging_setup.py` (ADR-012): `layer`, `run_id`, `skill_id`, `model`, `elapsed_s`, `input_tokens`, `output_tokens`, `cost_usd_estimate`, `cache_hit`. The client measures `elapsed_s` around the dispatch itself (not cache hits) and surfaces it on `ClaudeResponse.elapsed_s` so the orchestrator can copy it into `data/log/claude_calls.jsonl` without re-timing.

**Day-2 scope note.** The Day-2 client (`IMPLEMENTATION_PLAN.md § 3`) implements only: dual-mode, replay log, per-run USD kill-switch, concurrency semaphore, tenacity backoff, and call-level logging. Request coalescing, structured-output schema enforcement, quarantine, token-bucket rate limiter, and per-call prompt+max_tokens ceiling (§ 5.5 pkt 1) are deferred until a concrete call site requires them. This is enforced in the module docstring of `claude_client.py`.

Model selection (ADR-009): Opus 4.6 for L1 classification (resolved via the 2026-04-22 three-way pilot — see ADR-009 "L1 pilot findings"); Opus 4.7 for L2 (structure extraction, hardest step), L5 (reconciliation), L7/L8 (generation) — these remain on the original plan pending their own pilots; Sonnet 4.6 for L4 audits where throughput matters more than depth. Model choice is per-skill via `RunContext.model_config`; fallback to a single model if cost/rate limits force it.

### 5.3 Parallelism

Layer 4 is the only layer worth parallelizing: `|clusters| × 6 skills` independent calls, typically 30–48 calls. Implemented with `asyncio.gather` bounded by a semaphore (default 6 concurrent). Cache reuse means re-runs after a prompt tweak only re-cost the affected skill.

Layer 8 is sequential by construction (each iteration depends on the previous best). No parallelism there.

### 5.4 Run context

```python
class RunContext(BaseModel):
    run_id: str                 # e.g. "2026-04-23_pilot_2"
    seed: int                   # RNG seed (clustering determinism)
    model_config: dict          # {skill_id: model_name}
    meta_weights: dict[str, float]      # L6 knobs
    optimization_budget: int = 8
    convergence_patience: int = 3
    quality_ceiling: int = 90
    active_skills: list[str]    # enables fallback from 6 → 4 skills
```

Serialized to `data/derived/run_context/{run_id}.json`. Every artifact references `run_id`; multiple runs can coexist in the repo.

### 5.5 Cost budgeting and kill-switch (ADR-009, ADR-012)

A full pipeline run on 60 reviews fans out to roughly `|reviews| × L1..L3 fixed` + `|clusters| × 6 skills × L4` + `L5..L6 fixed` + `8 iterations × L7..L8` calls. At Opus-for-audits / Sonnet-for-classification pricing this is non-trivial and the hackathon budget is finite, so cost is a first-class budget alongside time and tokens.

Budgeting is enforced at three scopes:

1. **Per-call ceiling.** `claude_client.py` refuses any request whose estimated prompt + `max_tokens` exceeds a per-skill ceiling (default 8k in / 2k out, overridable via `skill_config.yaml`). Refusal is loud — raises, does not silently truncate.

2. **Per-run USD ceiling.** `RunContext` carries `usd_ceiling: float = 15.0` (tunable). Every response logs `cost_usd` computed from model + token count (table in `src/auditable_design/pricing.py`). The orchestrator tracks a running total in `data/log/cost.jsonl`; crossing the ceiling trips the **kill-switch**: in-flight coroutines complete, no new calls are dispatched, the run is marked `halted_budget` and whatever artifacts exist are preserved. Replay runs bypass this (zero cost).

3. **Layer 8 early-stop.** Optimization is the only layer that can burn cost without bound if convergence fails. `convergence_patience=3` (§5.4) already bounds iterations by quality; `optimization_budget=8` bounds them by count. Both are audited by `scripts/check_budget.py` before a run starts, which refuses to launch if `budget × avg_iter_cost > 0.6 × usd_ceiling`.

The ceiling is a brake, not a target. A healthy pilot run on 20 reviews should land well under $3; the ceiling exists so an unattended run that enters a pathological retry loop does not eat the whole hackathon credit pool.

---

## 6. Skill architecture (P2, P3)

Each skill is a directory with the standard structure already present in the author's existing skill library:

```
skills/<skill-name>/
├── SKILL.md                    # pre-prompt, triggers, constraints
├── rubric.md                   # scoring rubric (0–10 definitions per heuristic)
├── examples/
│   ├── input_example.json
│   └── output_example.yaml
└── schema.json                 # JSONSchema of expected output
```

**Authoring discipline for audit skills.** All six canonical skills share the same output schema (§4.5). SKILL.md files differ only in: (a) the heuristic vocabulary, (b) the reasoning style the skill is coached to adopt (Norman's cognitive-observational vs Cooper's patterns-oriented vs WCAG's criteria-driven), (c) worked examples. The schema uniformity is what makes the audit layer composable.

**New skills authored in the hackathon window:**

1. `structure-of-complaint` (Layer 2) — novel contribution. Typed node/edge vocabulary defined in §4.3.
2. `sot-reconcile` (Layer 5) — SOT methodology adapted to reconcile audit verdicts.
3. `audit-accessibility` — WCAG 2.2 + Inclusive Design Principles + cognitive accessibility notes. This is a non-negotiable canonical skill per concept §15.

**Skills reused from author's existing library (re-implemented from methodology, not copied code per concept §0):**

4. `audit-usability-fundamentals` — Norman
5. `audit-interaction-design` — Cooper
6. `audit-ux-architecture` — Garrett
7. `audit-decision-psychology` — Kahneman
8. `audit-business-alignment` — Osterwalder

Per concept §0: methodological patterns carry over; no pre-existing code is reused. Each skill's SKILL.md is re-authored within the event window, committed with a signed timestamp in git history.

---

## 7. Optimization loop (Layer 8) — detail

The loop is implemented in `src/layers/l8_optimize.py`; this section specifies its contract precisely because it is the core novelty (concept §11).

### 7.1 State machine

```
init ──► propose ──► audit ──► score ──► decide ─┬─► accept ──► propose  (loop)
                                                  ├─► dismiss ─► propose  (loop)
                                                  └─► stop ───► render
```

### 7.2 Dominance check (`src/evaluators/pareto.py`, ADR-008)

```python
def dominates_within_skill(score_new: dict, score_best: dict) -> bool:
    # strict pareto within a single skill's heuristic vector
    ...

def dominates(score_new: dict[str, dict], score_best: dict[str, dict],
              active_skills: list[str]) -> bool:
    # candidate dominates iff it dominates within every active skill
    return all(dominates_within_skill(score_new[s], score_best[s]) for s in active_skills)

def weighted_sum_improves(score_new: dict, score_best: dict,
                          weights: dict, max_regression: int = 1) -> bool:
    # accepts only if no single heuristic regresses by more than max_regression
    ...
```

Per-skill dominance is primary (ADR-008); weighted sum is fallback. Note that severity scales across skills are **not** numerically comparable even though both use 0–10 — the anchored rubrics mean within-skill comparisons are meaningful while cross-skill sums are only defensible with explicit meta-weights. The per-skill conjunction is mathematically stricter than naive concatenated pareto and avoids the scale-collision trap. Weighted-sum fallback implements concept §11 exactly — "improvement under weighted sum is accepted only if no single heuristic regresses by more than 1 point."

### 7.3 Append-only log

`data/log/optimization.jsonl` — one `OptimizationIteration` record per line, fsync'd after each append. The log survives process crashes and is the literal source for Layer 10's timeline view.

### 7.4 Anti-gaming safeguards (concept §11)

- **Fresh audit contexts.** Each audit Claude call starts from system prompt + spec only. No "this is iteration 5" context leak. Enforced by `claude_client` refusing to pass iteration metadata into audit skill invocations.
- **Spec-level iterations.** L8 operates on `DesignDecision` + spec JSON, not on rendered HTML. L9 generates HTML once from the winning iteration. This both reduces cost and prevents visual overfitting.
- **Manual spot-check at convergence.** The IMPLEMENTATION_PLAN reserves time on Day 4 for the author to sanity-check that the final design visibly solves the original complaint.

---

## 8. Demo surface (Layer 10 + baseline comparison)

### 8.1 Build model

Static SPA (Vite + React + Tailwind + shadcn/ui). All data is pre-computed during the pipeline run and baked into JSON bundles under `demo/public/data/`. `scripts/build_demo_bundle.py` copies the frozen outputs from `data/derived/` and `data/log/` into the demo public folder.

A minimal FastAPI dev server is added **only** for the meta-weights slider (§9 concept.md) if recomputing priorities in-browser proves fragile. Initial implementation does the recomputation client-side from a pre-baked `score_matrix.json` — no backend required.

### 8.2 Views

- **`TimelineView.tsx`** — Layer 10 View A. D3 for the vertical trajectory with clickable iteration cards.
- **`RationaleView.tsx`** — Layer 10 View B. D3 force-directed or layered DAG; click-through to iteration provenance.
- **`CompareView.tsx`** — baselines B1/B2/B3 side-by-side (concept §14). Three panels; same input insight.
- **`MetaWeightsPanel.tsx`** — sliders for the five meta-weights; re-ranks clusters live.

### 8.3 D3 integration

D3 is used inside React via refs (standard pattern — React owns the DOM envelope, D3 owns the SVG interior). Graph data models map 1:1 to `EvolutionNode` / `EvolutionEdge`.

---

## 9. Fallback architecture

The concept §15 fallback priority is encoded in `RunContext.active_skills` and feature flags. Each cut is a configuration change, not a code change — this is the payoff of the uniform contract (P3).

| Concept fallback | Architectural mechanism |
|---|---|
| Drop Cooper + Garrett | Remove from `active_skills`; L4 runs 4 skills instead of 6 |
| Drop validation double-pass | `RunContext.validation_passes = 1`; L6 skips consistency check |
| Static SVG instead of D3 | `TimelineView` and `RationaleView` switch to `<img src="...svg">` fallback, SVG pre-rendered by pipeline |
| Wireframe SVG instead of HTML/React prototype | L9b emits SVG+spec instead of HTML; `demo/` renders `<img>` |
| Only B3 baseline | `CompareView` detects missing B1/B2 bundles and renders single-panel |

**Non-negotiable skills** (concept §15) are hardcoded in `RunContext.active_skills` defaults; removing any of them requires editing the code path, which is a deliberate friction gate.

---

## 10. Reproducibility & evidence (ADR-007, ADR-011)

Every `AuditVerdict`, `DesignDecision`, and `OptimizationIteration` record captures:

- `claude_model` used
- `run_id` under which it was produced
- timestamp
- `skill_hash` — `sha256` over the **entire skill directory** (SKILL.md + all `references/*.md` + `rubric.md` + `schema.json` + `examples/*`), not just SKILL.md alone (ADR-003).

**Replay log (ADR-011).** `data/cache/responses.jsonl` is an append-only log, one line per Claude call. At submission time, `scripts/generate_replay_manifest.py` produces `data/cache/responses.manifest.sha256` — one hash per entry plus a tree hash — so reviewers can verify the log hasn't been tampered with.

The **canonical key hash** is:

```
key_hash = sha256(
    "\t".join([skill_id, skill_hash, model, repr(float(temperature)), str(int(max_tokens))])
    + "\x00"
    + "\x00".join([system, user])
)
```

Three things to note. `system` and `user` are hashed separately (with a `\x00` separator) rather than after concatenation — this avoids accidental collisions if boundaries shift. `max_tokens` is part of the key because the same prompt truncated at different budgets is semantically different evidence (a clipped verdict is not the same as a full one). Stored in the JSONL line, however, the `prompt` field is the **concatenated** form (`SYSTEM:\t{system}\tUSER:\t{user}`) so a reviewer inspecting `responses.jsonl` sees exactly what was sent without reconstructing it from two fields.

**Two client modes.** `RunContext.client_mode` selects:

- `"live"` — uses Anthropic API; appends to the replay log
- `"replay"` — resolves only from the replay log; missing entries raise `ReplayMiss` rather than silently falling back

Reviewers run in `"replay"` mode without needing an API key and get byte-identical outputs. Running `"live"` with a fresh key produces in-distribution equivalent — not byte-identical — outputs; this is stated openly in the README.

### 10.1 Observability (ADR-012)

Two log streams:

- `data/log/pipeline.log` — one JSON line per layer invocation: `layer`, `run_id`, `elapsed_s`, `status`, `output_record_count`.
- `data/log/claude_calls.jsonl` — one JSON line per Claude call: `call_id`, `layer`, `skill_id`, `skill_hash`, `model`, `elapsed_s`, `input_tokens`, `output_tokens`, `cost_usd_estimate`, `cache_hit`.

Pricing constants live in `src/auditable_design/pricing.py`. `scripts/log_summary.py` produces a one-shot run breakdown (time, cost, cache hit rate per layer) — essential for Day 4 performance triage.

---

## 11. Deployment topology (ADR-006, ADR-011, ADR-013)

### 11.1 Three physical locations, one data flow

```
[author's machine]                  [GitHub]                    [reviewer/jury]
 Python 3.11 + uv               ┌──────────────────┐         ┌──────────────┐
 anthropic SDK ── Opus 4.7 API  │ public repo       │         │ browser       │
 src/pipeline.py ─► data/       │ ── main branch    │  CDN    │ static demo   │
   derived/                     │ ── replay log     │ ──────► │ zero API      │
   artifacts/                   │    + manifest     │         │ zero key      │
   cache/responses.jsonl        │ ── /docs          │         │ client-side JS│
                                │ ── gh-pages       │         └──────────────┘
 demo/ (Vite+React+D3)          │    (demo/dist)    │
   ── npm run build ──►         │                   │
      demo/dist/                │ Actions: build +  │
                                │ integrity checks  │
                                └──────────────────┘
```

Three locations, one direction of flow. Heavy work (Claude calls, embeddings, clustering) happens exclusively on the author's machine. Only frozen artifacts and the replay log travel to GitHub. The reviewer's browser loads a static bundle and runs pure client-side JavaScript — no API key, no live LLM, no backend.

### 11.2 Client modes

Exactly two runtime modes exist for the pipeline:

- **`live`** — real Anthropic API calls; appends to `data/cache/responses.jsonl`. Used only on the author's machine.
- **`replay`** — resolves from `responses.jsonl` only; `ReplayMiss` on any uncached call. Used for reviewer reproducibility and for CI.

The demo itself is a **third thing** — a static bundle that consumes pre-frozen JSON. It does not invoke either mode; it does not know Claude exists.

### 11.3 What is published and what is not

Published to `main` (public):

- `concept.md`, `ARCHITECTURE.md`, `IMPLEMENTATION_PLAN.md`, `README.md`, `docs/`
- All source under `src/`, `skills/`, `scripts/`, `demo/`
- `data/cache/responses.jsonl` + `responses.manifest.sha256` — replay log pinned for reproducibility
- `data/raw/corpus.jsonl` — public Google Play data, author-hashed
- `data/derived/**` and `data/artifacts/**` — frozen pipeline outputs for the submission run

Not published (enforced via `.gitignore`):

- `.env` / `.env.local` — never committed; pre-commit hook scans for `sk-ant-` prefixes (ADR-013)
- `data/log/**` — verbose per-call logs may contain prompts; kept local
- `data/quarantine/**` — failed structured-output records; kept local for debugging
- `demo/node_modules/`, `demo/dist/`, `demo/.vite/`

**Replay log sensitivity.** `data/cache/responses.jsonl` is published to the public repo and contains, verbatim, the full `system` and `user` prompts of every Claude call plus the full model response. For this submission the prompt content is derived exclusively from `data/raw/corpus.jsonl` — public Google Play data with author-hashed usernames — so the replay log is safe to publish. **Anyone reusing this client** on non-public content (internal feedback, support tickets, user emails, anything with PII) must either scrub inputs before calling `Client.call`, mark `responses.jsonl` as local-only in their own `.gitignore`, or both. The replay log is exactly as sensitive as the prompts the caller hands it; the client does not redact.

Published to `gh-pages` branch by Actions (public, CDN-fronted):

- Contents of `demo/dist/` after `npm run build`
- Includes `demo/public/data/*.json` baked from frozen artifacts by `build_demo_bundle.py`

### 11.4 CI workflow

`.github/workflows/pages.yml` has three jobs, none of which require `ANTHROPIC_API_KEY`:

1. **`verify-integrity`** (runs on every push and PR) — recomputes the replay-log manifest and compares to `data/cache/responses.manifest.sha256`. Any drift fails the build. This closes the cache-poisoning surface that public-repo PRs create (ADR-011).
2. **`build-demo`** (on push to `main`) — `npm ci`, `npm audit --production --audit-level=high`, `npm run build`; uploads `demo/dist` as an artifact.
3. **`deploy-pages`** (on push to `main`, depends on `build-demo`) — uses `actions/deploy-pages@v4` to publish the artifact.

`npm ci` enforces `package-lock.json`; `npm audit` fails the build on high-severity supply-chain advisories. Dependabot is enabled for `npm` and `pip` to catch upstream security fixes within the event window without manual attention.

### 11.5 What a reviewer sees

Two reviewer paths, both deterministic:

- **Open the demo URL** — click through TimelineView, RationaleView, CompareView, MetaWeightsPanel; see pre-frozen artifacts; zero API calls.
- **Clone the repo and run** — `uv sync && python -m src.pipeline --mode replay` reproduces every artifact byte-identical from the replay log; no Anthropic key needed.

This is the strongest reproducibility story compatible with the hackathon's "auditable" thesis.

## 12. Out-of-scope (explicit)

Per concept §7 (future directions) and general hackathon scope:

- Plugin registry for arbitrary third-party audit skills (post-hackathon)
- Multi-app corpus ingestion (Duolingo only)
- Multi-language review support (English only; other-language reviews filtered at ingest)
- Authentication, multi-user runs, cloud deployment
- Live review scraping during demo (corpus is frozen on Day 1)

---

## 13. Mapping to concept.md

| Concept section | Architecture section |
|---|---|
| §4 Layer 1 classification | §3 `l1_classify.py`, §4.2 schema |
| §5 Layer 2 structure-of-complaint | §3 `l2_structure.py`, §4.3 schema, §6 skill |
| §6 Layer 3 aggregation | §3 `l3_cluster.py` + `l3b_label.py`, §4.4 schema and label lifecycle, §3 `embedders/` |
| §7 Layer 4 six canonical audits | §3 `l4_audit.py`, §4.5 schema, §6 skill directory |
| §8 Layer 5 SOT reconciliation | §3 `l5_reconcile.py`, §4.6, §6 `sot-reconcile` |
| §9 Layer 6 business weighting | §3 `l6_weight.py`, §4.7, §8.2 `MetaWeightsPanel` |
| §10 Layer 7 decision generation | §3 `l7_decide.py`, §4.8 |
| §11 Layer 8 optimization loop | §3 `l8_optimize.py`, §7 |
| §12 Layer 9 final redesign | §3 `l9_render.py`, §4.8, §9 fallback chain |
| §13 Layer 10 evolution graph | §3 `l10_evolution.py`, §4.9, §8.2 `TimelineView`/`RationaleView` |
| §14 Baseline comparison | §3 `scripts/` + §8.2 `CompareView` |
| §15 Fallback strategy | §9 |
| §16 External risk defenses | §4 (author_hash), §10 (evidence) |

---

**End of architecture document.**

*Deviations during implementation are reflected back into this document and `concept.md` in the same commit.*
