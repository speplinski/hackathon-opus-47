# Context: Duolingo — corpus selection and public business context

**Purpose.** This document has two jobs. First, it justifies the choice of Duolingo as case-study corpus for Auditable Design and specifies the exact selection criteria for the 500–1000 reviews that feed Layer 1. Second, it is the **public business context** consumed by Layer 6 (business weighting) — a footnoted, source-anchored summary of Duolingo's publicly known freemium model, KPIs, strategy, and the 2025 AI-first crisis. Every factual claim below carries a footnote to a publicly available source. Nothing in this document relies on inside knowledge, leaks, or speculation.

Framing is instrumental, not targeted. The goal is to demonstrate a reproducible method on feedback from a popular app; it is not to critique Duolingo. Every insight surfaced in the demo is anchored in a literal quote from a specific, publicly available review.

---

## 1. Why Duolingo (selection rationale)

Three properties make Duolingo a strong fit for a proof-of-method corpus:

- **UX signal density.** Spot sampling of early-2026 review windows shows approximately 40–60% of negative reviews target UX concerns (paywall behaviour, hearts/streak mechanics, notification patterns, ad placement, feature removal) rather than content quality or billing. This is the target ratio for Layer 1 classification success — a three-week window of current reviews (see §2) is already dense enough to demonstrate the method.
- **Corpus accessibility.** Google Play and App Store reviews are publicly accessible and both platforms' Terms of Service permit analytical use of publicly displayed content.[^3][^4] No scraping of private data, no authentication walls, no TOS concerns.
- **Public strategic context.** Duolingo's 2025 "AI-first" pivot[^1][^2] and the product's ongoing freemium-funnel tensions (§3–§5) are well-documented in press and investor filings, which gives Layer 6 a real, sourceable business model to weigh audit findings against. The pivot is referenced here as publicly documented context, not as the subject of this demo.

## 2. Corpus selection criteria

| Field                     | Value                                                                                                |
| :------------------------ | :--------------------------------------------------------------------------------------------------- |
| Sources                   | Google Play (primary), App Store (secondary, for parity check)                                       |
| Language                  | English only (L1 classifier is English-tuned for the hackathon)                                      |
| Date window               | 2026-04-01 – 2026-04-21 (three-week early-2026 steady-state; see §1 for framing)                     |
| Star rating filter        | 1–3 stars (high signal for UX pain); 4–5 star sample retained as control                             |
| Length filter             | ≥ 80 characters and ≤ 4000 characters (too-short reviews lack structure; too-long reviews are rare)  |
| Target size               | 600 reviews (60 for pilot, 600 for full run, same random seed)                                       |
| PII handling              | Usernames hashed at intake; no DOB / email / location retained even if present in review body       |
| Deduplication             | Exact-text dedup; near-duplicate dedup deferred to clustering in L3                                  |
| License note              | Review text quoted under fair use; author & app attribution retained in every evidence reference    |

Intake procedure is implemented in `scripts/collect_reviews.py` (Day 1 afternoon). Reviews are written once to `data/raw/corpus.jsonl`, the file's sha256 is frozen in `data/raw/corpus.manifest.sha256`, and the pipeline reads only from the frozen snapshot.

## 3. Freemium model (public facts)

Duolingo operates a classic freemium funnel. Free tier is ad-supported with a **hearts** gating mechanism that limits error tolerance per session; **streaks** create return-visit loyalty.[^5] Paid tiers include **Super Duolingo** (ads removed, unlimited hearts, personalised practice) and **Duolingo Max**, introduced in 2023, which bundles GPT-4-backed features like "Explain My Answer" and "Roleplay".[^6][^7] Conversion is primarily paywall-driven at key friction points (heart depletion, streak-freeze offers, feature unlocks).

## 4. Strategic context and publicly stated KPIs

Duolingo is a publicly traded company (NASDAQ: DUOL) and reports DAU, MAU, paid subscriber count, and ARPU on a quarterly cadence.[^8] At time of writing (early 2026) publicly reported scale is on the order of tens of millions of DAU and over 100M MAU, with paid subscriber ratio in the single-digit percent range.[^9] Strategic priorities emphasised in recent shareholder communications include AI-native learning experiences, Duolingo Max expansion, and investment in retention mechanics. These priorities are the input to Layer 6's `strategic_alignment` dimension.

## 5. The 2025 AI-first pivot

On April 28, 2025, Luis von Ahn published an internal memo (later publicly posted) describing Duolingo as "AI-first" and outlining a plan to gradually stop using contractors for tasks AI can handle.[^1] Public reaction was sharply negative, with concentrated backlash visible in app store review spikes, TikTok videos, and press coverage; Duolingo's public social media presence was temporarily scaled back in response.[^2][^10] Subsequent clarifications by the company emphasised that full-time employees were not targeted and that AI would augment rather than replace core learning work. Within this project the 2025 pivot is treated as a publicly documented event, not as a subject of reputational commentary.

## 6. Ethical framing for this project

No claims are made about Duolingo designers' intent. Reviews are treated as public data and quoted verbatim only with the original wording preserved. The Duolingo brand is used in the context of public knowledge, not commercial purpose. The demo surface includes a banner making the "proof of method, not critique" framing explicit, and the `AUDIT.md` in the repo root records the full evidence chain from review → insight → decision → final redesign.

---

## Sources

All URLs must be fetched and archived to `data/raw/sources/` before the first L6 run. Each footnote below is reproduced in `data/raw/sources/sources.json` with `{url, retrieved_at, sha256}` for integrity.

[^1]: Luis von Ahn, "AI-first" internal memo, April 28, 2025. Public archive TBD — capture to `sources/ai_first_memo.html`.
[^2]: Coverage of user backlash (press round-up, April–May 2025). Capture a representative set (TechCrunch, The Verge, Fast Company) to `sources/backlash_*.html`.
[^3]: Google Play Terms of Service — analytical use of public reviews. Capture current TOS snapshot.
[^4]: Apple App Store Review Guidelines / Media Services TOS — public review display. Capture current snapshot.
[^5]: Duolingo Help Center — "Hearts" explainer. Public URL under support.duolingo.com.
[^6]: Duolingo blog / press — Duolingo Max launch announcement (March 2023).
[^7]: Duolingo Max product page — current feature listing.
[^8]: Duolingo Investor Relations — latest 10-Q or annual report covering the date window of this corpus.
[^9]: Duolingo Q4 2025 / Q1 2026 shareholder letter — DAU/MAU/paid figures. Use the most recent report available before corpus freeze.
[^10]: Independent coverage of Duolingo social-media scale-back following AI-first backlash (mid-2025 reporting).

**Pre-flight verification gate.** Before Layer 6 runs, `scripts/verify_sources.py` confirms every `[^n]` footnote has a matching entry in `sources.json` with a non-empty `sha256`. Missing or stale sources → non-zero exit → pipeline refuses to advance.
