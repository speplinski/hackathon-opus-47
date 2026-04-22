# Auditable Design — Implementation Plan

**Day-by-day plan for the 21–26 April 2026 hackathon window.**

**Author:** Szymon P. Pepliński
**Companion documents:** `concept.md` (source of truth), `ARCHITECTURE.md` (how layers compose)
**Submission deadline:** Monday 27 April 2026, 02:00 CEST (= Sun 26 Apr 20:00 EDT).

---

## 0. Ground rules

**GR1 — Spine first, width later.** End of Day 2: a thin end-to-end path exists (10 reviews → 1 cluster → 1 verdict → 1 decision → 1 iteration). Days 3–5 widen the spine. No layer is built "fully" before the next one is stubbed.

**GR2 — Checkpoint, not guesswork.** Each day ends with an explicit yes/no on the daily checkpoint. If the checkpoint fails, the next day starts with the corresponding fallback from concept §15, not with "more effort on the same thing."

**GR3 — Saturday is fallback day, not fix day.** Concept §15 hard rule. Any new feature proposed on Saturday is rejected unless it directly closes a gap already surfaced earlier in the week.

**GR4 — The cache is sacred.** `data/cache/` is committed frequently. Burning Claude budget on re-runs of already-answered prompts is the #1 avoidable waste.

**GR5 — Commit discipline.** Every non-trivial change pushed to `origin/main` with a conventional-commit prefix (`feat:`, `fix:`, `data:`, `skill:`, `demo:`, `doc:`). Timestamps in git history are evidence that the build happened during the event window (concept §0).

---

## 1. Pre-flight (Day 1 morning, 21 April)

Before coding: the "items 2–4" from concept §19 need to exist as files on disk.

**Deliverables (before 13:00):**

- `ARCHITECTURE.md` — done (this companion doc).
- `CONTEXT_DUOLINGO.md` — ~500 words, public context with footnotes. Five sources minimum: Duolingo's AI-first announcement and backlash coverage, freemium KPIs reporting, public teardown of the current paywall, Google Play review distribution snapshot, European Accessibility Act reference (for the §7 accessibility framing).
- Repo skeleton — `pyproject.toml`, `package.json`, directory tree per ARCHITECTURE §3, `.gitignore` for `data/cache/` (reversed at submission per ARCHITECTURE §10), empty `__init__.py` files, stub `pipeline.py` that prints the DAG.

**Tool setup (parallel, afternoon):**

- `uv` environment with Python 3.11
- Anthropic SDK, `pydantic`, `sentence-transformers`, `hdbscan`, `numpy`, `scikit-learn`
- Vite + React + Tailwind + shadcn/ui scaffold in `demo/`
- D3 installed
- `.env` with API key, never committed

**Day 1 checkpoint:** skeleton compiles and `python -m src.pipeline --dry-run` prints the ten layer names. Demo SPA renders "Hello, Auditable Design." Corpus file placeholder exists (actual collection starts now in parallel).

---

## 2. Day 1 — Tuesday 21 April — Foundation

**Deliverables:** `ARCHITECTURE.md` with 15 ADRs, `CONTEXT_DUOLINGO.md`, repo scaffold
(`pyproject.toml`, `uv.lock`, directory tree), `src/auditable_design/storage.py`
(atomic writes + sidecar hash + perimeter check), `src/auditable_design/prompt_builder.py`
(three-layer injection defense), `src/auditable_design/schemas.py` (Pydantic models for
all 10 layers), CI (Pages workflow, Dependabot, pre-commit with gitleaks, relock workflow),
test suite green.

**Commit checkpoint:** `feat: scaffold + storage/prompt/schemas with tests`.

---

## 3. Day 2 — Wednesday 22 April — Corpus, L1, thin spine start

**Goals:** `claude_client.py`, corpus collection, L1 classification on pilot, L2 skill draft.

### `claude_client.py` (minimal)

One file, one `Client` class, dual-mode (live/replay) per ADR-011. Uses `tenacity` for
backoff, `asyncio.Semaphore(6)` for concurrency limit, an `asyncio.Lock()`+counter for
the per-run USD kill-switch (ADR-015). Explicitly NOT built yet: request coalescing,
quarantine dir, token-bucket rate limiter — add only when a concrete call site needs them.

### `scripts/collect_reviews.py` + run

Google Play scraper (`google-play-scraper` lib), English only, 2026-04-01 → 2026-04-21,
target 600 reviews (CONTEXT §2). Output: `data/raw/corpus.jsonl` + sha256 manifest
(`data/raw/corpus.manifest.sha256`). Idempotent (dedup by `review_id`). Kick off the
full scrape to run in background.

### `src/auditable_design/layers/l1_classify.py`

Rubric in system prompt, JSON output via `response_format`, batch of 10 reviews/call.
Run L1 on 20-review pilot sample first (task #19). Manual spot-check.

### L2 skill authoring starts

`skills/structure-of-complaint/SKILL.md` — node types, relation types, verbatim-quote
constraint. 150-word SKILL.md + one worked example, not a literary treatise.

**Day 2 checkpoint:** pilot 20 reviews classified, L1 split in 40–60% window, spot-check PASS.
Full-corpus L1 can run overnight.

**Commit target:** `feat: claude_client + L1 classification on pilot sample`.

---

## 4. Day 3 — Thursday 23 April — Thin spine

**Goals:** L2 skill + L2 implementation applied on a 50-review subset of L1 output,
plus L3 clustering and spine stubs for L4/L7/L8. End Thursday with
`data/derived/l2/*.jsonl` existing (rejection rate under 10%) and
`python -m src.pipeline --run thin-spine` producing one decision + one iteration.

### Morning — Layer 2 skill

- Author `skills/structure-of-complaint/SKILL.md`: node types (pain, expectation, triggered_element, workaround, lost_value), relation types, verbatim-quote constraint.
- Author `skills/structure-of-complaint/rubric.md` and 3 worked examples.
- Implement `src/layers/l2_structure.py`. Key code: the verbatim-substring validator (rejects nodes whose `verbatim_quote` is not a substring of the source review text).
- Run L2 on a 50-review sub-sample from UX-relevant output of L1. Inspect 5 graphs manually.
- Expected yield: 3–7 typed nodes per review, rejection rate under 10% on the substring validator.

### Early afternoon (14:00–16:00) — Layer 3 clustering

- `src/embedders/local_encoder.py` — sentence-transformers wrapper (model: `all-MiniLM-L6-v2` for speed, upgrade to `all-mpnet-base-v2` if H100 time permits). Embeds the `pain` and `expectation` nodes.
- `src/layers/l3_cluster.py` — HDBSCAN primary, KMeans (k=6) fallback. Cluster labels generated by Claude from top-5 representative quotes per cluster.
- Run on the 50-review sub-sample, inspect cluster labels.
- Expected yield: 5–8 clusters, each with minimum 20 reviews (on the full corpus scale — sub-sample will yield fewer).

### Late afternoon (16:00–19:00) — Spine stub

- `src/layers/l4_audit.py` — scaffold only. Call a **single** skill (Norman/`audit-usability-fundamentals`) on one cluster. Uniform contract output (ARCHITECTURE §4.5).
- `src/layers/l7_decide.py` — generate principle + decision from one cluster's reconciled (for now: only) verdict.
- `src/layers/l8_optimize.py` — scaffold the state machine, run **one** iteration (v_0 → v_1), don't worry about pareto yet.
- `data/log/optimization.jsonl` — first two rows exist.

### Evening — Day 3 checkpoint

- **Checkpoint:** `python -m src.pipeline --run thin-spine` produces one decision + one iteration, fully traced back to specific reviews. Artifacts present under `data/derived/` and `data/log/`.
- This is GR1. If the checkpoint fails, the next day does not start new work until the spine exists — Saturday is not a rescue for a missing spine.

**Commit target:** `feat: thin end-to-end spine on 50-review subset`.

---

## 4. Day 3 — Thursday 23 April — Width: six skills, reconciliation, weighting

**Goals:** L4 runs six canonical audits in parallel; L5 reconciles; L6 weights. First full-corpus end-to-end run. This is the concept §15 decision checkpoint day.

### Morning (09:00–13:00) — Six audit skills

- Author SKILL.md + rubric for all six canonical audit skills (three backbones first: Norman, Kahneman, Accessibility per concept §7 demo strategy).
- Uniform output schema — ARCHITECTURE §4.5 and concept §7. If SKILL authoring slips, only Cooper and Garrett are cuttable (concept §15); Norman, Kahneman, Osterwalder, Accessibility are non-negotiable.
- `src/layers/l4_audit.py` upgraded from scaffold to real implementation. `asyncio.gather` with concurrency 6 over `|clusters| × |active_skills|`.
- Dry-run on the 50-review subset first, full corpus after. Cache hits matter here.

### Afternoon (14:00–17:00) — Reconciliation + weighting

- `skills/sot-reconcile/` — SOT adapted for reconciling audit verdicts. Node types (thesis, claim, assumption, contradiction, gap), relation types (supports, contradicts, elaborates, evidences, assumes, questions).
- `src/layers/l5_reconcile.py` — consumes all verdicts for a cluster, produces `ReconciledVerdict` with ranked violations and explicit tensions between design schools.
- `src/layers/l6_weight.py` — 5-dimensional rubric, double-pass validation, median on third pass if delta > 1. Meta-weights read from `RunContext`.

### Late afternoon (17:00–20:00) — First full run

- Run the full pipeline L1–L7 on the full corpus. This is the moment cache design pays off.
- Inspect clusters, ranked violations, decisions.
- **Decision checkpoint (concept §15):** Is the system producing coherent, traceable insights on real data? If yes → proceed to L8 optimization on Day 4. If no → triage: re-prompt L4/L5/L6, adjust rubrics, do not touch L1–L3 unless clearly broken.

### Evening (20:00–23:00) — L8 polish

- Implement `src/evaluators/pareto.py` properly. Dominance check + weighted-sum fallback with `max_regression=1`.
- Run the optimization loop for one flagship cluster (paywall is the obvious pick — concept §18 flags it as the flagship decision). Target 3 accepted iterations by end of day.

**Commit target:** `feat: full pipeline L1–L7 + L8 on flagship cluster`.

---

## 5. Day 4 — Friday 24 April — Demo integration

**Goals:** Evolution graph views, baseline comparison, meta-weights panel. Pipeline stops being touched unless bugs surface.

### Morning (09:00–13:00) — Layer 10 views

- `src/layers/l10_evolution.py` — assembles `EvolutionNode`/`EvolutionEdge` records from the pipeline outputs and optimization log.
- `scripts/build_demo_bundle.py` — freezes the JSON bundles into `demo/public/data/`.
- `demo/src/views/TimelineView.tsx` — D3 vertical trajectory; clickable iteration cards reveal design artifact + scores + informing reviews.
- `demo/src/views/RationaleView.tsx` — final-design rationale graph with click-through to the iteration that produced each element.

### Afternoon (14:00–17:00) — Baselines + meta-weights

- `scripts/baseline_b1.py` — single-shot naive prompt. One Claude call, whole corpus, "propose a paywall redesign."
- `scripts/baseline_b2.py` — manual clustering (reuses L3 output) + single-pass generation (reuses L7, skip L8).
- `demo/src/views/CompareView.tsx` — three-panel side-by-side. Same insight, three outputs, four metrics from concept §14.
- `demo/src/views/MetaWeightsPanel.tsx` — sliders for the five meta-weights, live re-ranking client-side from `score_matrix.json`.

### Late afternoon (17:00–19:00) — Layer 9 render

- `src/layers/l9_render.py` — pareto-optimal iteration from the log, render spec JSON + HTML/React prototype + rationale bundle. Graceful fallback: if HTML generation fails, emit wireframe SVG + spec.
- Manual spot-check (concept §11 anti-gaming): does the final paywall redesign visibly address the original complaints?

### Evening (20:00–23:00) — Day 4 checkpoint

- **Checkpoint:** end-to-end demo is walkable. Open `demo/`, click through Timeline → iteration → reviews → rationale. Compare view shows B1/B2/B3. Meta-weights slider re-ranks clusters.
- **Fallback triggers:** if D3 is misbehaving → static SVGs rendered by pipeline (concept §15 item 3). If HTML prototype is unreliable → wireframe SVG (item 4). If a baseline slipped → show B1 vs B3 only (item 5).

**Commit target:** `feat: interactive demo with timeline + compare + meta-weights`.

---

## 6. Day 5 — Saturday 25 April — Polish, deploy, harden (FALLBACK DAY, GR3)

**Goals:** pitch artifacts, GitHub Pages deploy, security hardening, backup video. Explicitly no new pipeline features.

### Morning (09:00–13:00) — Pitch-facing deliverables

- `README.md` — front-door. Value proposition in 30 seconds (concept §18 success signal). What it is, how to run (both `replay` and `live` modes), one screenshot from TimelineView, link to the deployed demo URL.
- `docs/technical_writeup.md` — 1500–2000 words. Structure: problem → 10 layers → optimization loop detail → baseline metrics → security posture → what I'd build next.
- Pitch script (~3 min): hook (Duolingo AI crisis), demonstration (timeline view), differentiation (traceability + optimization), close (Auditable Design as methodology).

### Early afternoon (14:00–16:00) — Deploy to GitHub Pages (ADR-013)

- Confirm `.github/workflows/pages.yml`, `.github/dependabot.yml`, and `.gitignore` are committed.
- `demo/vite.config.ts` — set `base: '/hackathon-opus-47/'` for subpath hosting.
- `demo/public/data/` — ensure bundled JSON is produced by `scripts/build_demo_bundle.py` from frozen artifacts (no pipeline rerun needed).
- Push to `main`; watch the three CI jobs succeed: `verify-integrity`, `build-demo`, `deploy-pages`.
- Settings → Pages → Source = GitHub Actions. Settings → Branches → protect `main` (require CI + linear history).
- Resulting demo URL — verify load from a cold browser profile, verify DevTools network tab shows zero anthropic.com traffic.

### Late afternoon (16:00–18:00) — Security hardening sweep (see `docs/SECURITY.md`)

Run the findings table end-to-end and confirm each is closed:

- V-01 — Gitleaks clean on full history; pre-commit hook active; CI job green.
- V-02 — `scripts/generate_replay_manifest.py` produced current manifest; `verify_replay_manifest.py` exits 0; CI `verify-integrity` green on the current `main`.
- V-03 — grep the codebase for raw user-text f-strings into prompts: `grep -rn 'f".*{review' src/` returns nothing.
- V-05 — ESLint passes with `react/no-danger: error`; CSP meta present in `demo/index.html`; `stripTags` applied in bundle builder.
- V-07 — `npm audit --audit-level=high` clean; Dependabot enabled and listing no open high-severity PRs.
- V-09 — PII grep on `data/raw/corpus.jsonl` for emails, phone numbers, `I am` statements; quarantine any hits.
- V-15 — `npm test` passes the Python↔TS weighted-sum parity test.

### Evening (18:00–23:00) — Backup video + final polish

- Screen recording of the full demo walkthrough (~2 min), recorded from the live GitHub Pages URL (not localhost). Insurance against demo failure during judging.
- Dry-run the submission from a fresh clone on a clean machine: does `uv sync && python -m src.pipeline --mode replay` reproduce the artifacts byte-identical? Fix any non-hermetic dependencies.
- Verify every non-negotiable from concept §15 is present: `structure-of-complaint`, business weighting with rubric, optimization loop with append-only log, evolution graph, accessibility as canonical perspective, honest framing.
- Tighten demo copy. Remove developer-facing strings. Every Claude-generated cluster label and decision name reads like something a human would say.
- Walk the demo with fresh eyes. One strong demo moment (concept §18): timeline view with a clear "watch the design improve itself through audit."
- If any deliverable is still missing at 23:00: stop, cut it, and document the cut openly in `README.md` under "Known scope reductions." Honest framing (concept §15) is a non-negotiable.

**Do not add features after 23:00 Saturday.**

---

## 7. Day 6 — Sunday 26 April — Submission

### 00:00–01:30 — Final verification

- Fresh clone, `uv sync`, `npm install`, `python -m src.pipeline --run final`, open `demo/`. Every view renders.
- All links in README resolve.
- Backup video is uploaded to a reliable host (linked from README).
- Concept §19 checklist items all exist: concept.md, ARCHITECTURE.md, CONTEXT_DUOLINGO.md, repo skeleton.
- Submit before 02:00 Polish time.

### 01:30–02:00 — Submit

- Push final commit with tag `v1.0-submission`.
- Submit through the hackathon portal.
- Post submission thread (if required by the event).

---

## 8. Task dependency graph (abbreviated)

```
Day 1: [pre-flight] → [corpus] → [L1]
Day 2: [L2 skill] → [L2] → [L3] → [spine stubs L4/L7/L8]    ← GR1 checkpoint
Day 3: [5 remaining audit skills] → [L4 real] → [sot-reconcile skill] → [L5] → [L6]
       → [first full run]                                    ← §15 decision checkpoint
       → [L8 on flagship cluster]
Day 4: [L10 assembly] → [Timeline+Rationale] → [baselines] → [Compare+MetaWeights]
       → [L9 render] → [spot-check]
Day 5: [README+writeup+pitch] → [backup video] → [cache commit] → [reviewer dry-run]
       → [polish + fresh-eyes walk]
Day 6: [final verification] → [submit]
```

---

## 9. Risk-triggered fallback ladder (concept §15 encoded)

Executed in this exact order when time pressure bites. Each step is reversible until it is committed.

1. Drop Cooper + Garrett audit skills (reduce L4 to 4 skills: Norman, Kahneman, Osterwalder, Accessibility).
2. Drop double-pass validation in L6 (accept single-pass scores).
3. Replace D3 dynamic graphs with pre-rendered static SVGs.
4. Replace HTML/React prototype with wireframe SVG + spec.
5. Drop B1 and B2 baselines from demo (single-panel Compare view).

**Not on this ladder (non-negotiable):** `structure-of-complaint`, business weighting with rubric, optimization loop with append-only log, evolution graph, accessibility as canonical perspective, honest framing.

---

## 10. Tracking — one-line daily log

Add one line per day to `docs/daily_log.md`:

```
2026-04-21  L1 done, corpus @ N reviews, split X%/Y%, spot-check PASS
2026-04-22  L2+L3 done, thin spine green, 1 cluster → 1 decision → 1 iteration
2026-04-23  6 skills active, first full run done, decision checkpoint: PASS
2026-04-24  Demo walkable, baselines in place, Compare + MetaWeights live
2026-04-25  README+writeup done, backup video recorded, dry-run green
2026-04-26  Submitted at HH:MM
```

Terse. Honest. This is the journal the jury will not see but that keeps the work honest through the event window.

---

## 11. What "done" looks like (concept §18 encoded)

**Must-have for submission (matches concept §18 must-deliver):**

- Pipeline runs end-to-end on the full Duolingo corpus
- Minimum 4 canonical audit skills active (Norman, Kahneman, Osterwalder, Accessibility)
- Optimization loop logs at least 3 iterations for the flagship cluster
- Evolution graph for the paywall redesign
- Interactive demo (even if D3 is replaced with static SVG)
- At least B3 baseline in demo; B1 if time allowed
- README, technical write-up, backup video

**Nice-to-have (concept §18 nice-to-have):**

- All 6 canonical skills including Cooper and Garrett
- Functional HTML/React prototype (not wireframe fallback)
- Full validation double-pass in L6
- B1 + B2 + B3 in Compare view
- Multiple optimization trajectories for additional clusters

---

**End of implementation plan.**

*Any deviation from this plan during the event window is reflected back into this file and `concept.md` in the same commit.*
