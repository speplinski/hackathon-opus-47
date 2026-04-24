"""Verify-on-product — real-product grounding hook.

Takes L5 reconciled verdict (named heuristic violations inferred
from user reviews) plus real product screenshots, and verifies each
heuristic against what is actually visible on the product. Emits
grounded evidence per heuristic: confirmed/refuted/partial,
specific UI elements cited, and an adjusted severity.

This is the MVP of the "real-product hook" described in
docs/value_proposition.md § Scope — the current hackathon spine
operates on prose descriptions (before_snapshot / after_snapshot)
inferred by the L3b→L7 chain from user reviews. This script closes
the loop: the reconciled verdict's hypotheses are checked against
visible product state.

Full production version (v2 roadmap) would use MCP connectors to
code repo + Figma + analytics; this MVP uses a Claude VLM call on
screenshots + DOM snippets (future). For the hackathon: one VLM
call per cluster, three screenshots, seven heuristics.

Input:
- cluster_id (default cluster_02)
- L5 reconciled verdict (provides heuristic slugs + severities + violation descriptions)
- Screenshots directory with real product imagery

Output:
- data/derived/verify_on_product/cluster02_verification.json — per-heuristic grounded evidence
- data/derived/verify_on_product/cluster02_verification.md — human-readable artifact referencing screenshots
- data/derived/verify_on_product/cluster02_verification.provenance.json

Cost: one Opus 4.7 VLM call with 3 images + structured prompt ≈
$0.20–0.40 depending on image dimensions.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anthropic

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from auditable_design.claude_client import _omits_sampling_params  # noqa: E402
from auditable_design.layers.l6_weight import load_reconciled_verdicts  # noqa: E402
from auditable_design.schemas import ReconciledVerdict  # noqa: E402


DEFAULT_RECONCILED = (
    _REPO_ROOT
    / "data/derived/l5_reconcile/l5_reconciled_cluster02_opus46.jsonl"
)
DEFAULT_SCREENSHOTS_DIR = _REPO_ROOT / "data/raw/duolingo_screenshots"
DEFAULT_OUT_DIR = _REPO_ROOT / "data/derived/verify_on_product"
DEFAULT_MODEL = "claude-opus-4-7"

# Anthropic vision API limits image side to ~8000px and total prompt ≤200k tokens.
# Our screenshots are ≤2400px, well within bounds.
MAX_TOKENS = 4096


SYSTEM_PROMPT = """\
You are a senior UX research auditor. You have been given:

1. **A list of named heuristic violations** that were identified by
   a multi-lens audit pipeline (Norman, WCAG, Kahneman, Osterwalder,
   Cooper, Garrett) from an aggregated corpus of real user reviews.
   Each heuristic has a slug, a baseline severity from the
   reconciled verdict, and a short violation description.
2. **Real product screenshots** showing the surface the users
   complained about.

Your job is to **verify each heuristic against the actual product**.
For each heuristic, decide whether what the review-inferred
hypothesis says matches what is visible in the screenshots, and
emit grounded evidence citing specific UI elements.

You are NOT re-auditing from scratch. You are VERIFYING whether
the hypotheses hold in reality. If a screenshot shows no evidence
for a given heuristic, mark it `partial` or `refuted` rather than
inventing confirmation.

## Output format

Respond with ONLY a JSON object, no prose, no markdown fences:

```json
{
  "cluster_id": "<cluster_id>",
  "grounded_evidence": {
    "<heuristic_slug>": {
      "confirmed": "confirmed|partial|refuted",
      "evidence": "<1-3 sentences citing specific UI elements, positioning, copy, colors, sizes, z-index/modal behavior that are visible in the screenshots>",
      "adjusted_severity": <int in {0, 3, 5, 7, 9}>,
      "reasoning": "<1-2 sentences on why you adjusted severity up/down or kept it, based on what you see>"
    },
    ... (one entry per baseline heuristic)
  },
  "summary": "<3-4 sentences: which heuristics were confirmed on the product, which were partial/refuted, any defects you noticed that are not in the baseline list>"
}
```

## Constraints

- `grounded_evidence` keys must match the baseline heuristic slugs EXACTLY (no extras, no omissions).
- `adjusted_severity` must be in `{0, 3, 5, 7, 9}` (ADR-008 anchor).
- `evidence` must cite visible detail (e.g. "the pink 'SUPER — GET SUPER' CTA in top-right corner with checkmark", not "there is a call to action").
- Use `confirmed` when the screenshot shows exactly what the baseline claims.
- Use `partial` when the defect is visible but less severe, or present in a different form.
- Use `refuted` when the screenshot contradicts the baseline hypothesis.
- In `summary`, flag any defect visible in the screenshots that the baseline heuristic list did NOT name — this is valuable audit signal.

## Anchored severity (ADR-008)

- `0` — no violation present on the product
- `3` — cosmetic residue of a violation
- `5` — partial violation, surface-level
- `7` — structural violation, clearly present
- `9` — critical violation, dominates the surface
"""


def _sha256(path: Path) -> str:
    if not path.exists():
        return "missing"
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _short_model(model: str) -> str:
    return {
        "claude-opus-4-6": "opus46",
        "claude-sonnet-4-6": "sonnet46",
        "claude-opus-4-7": "opus47",
    }.get(model, model.replace("/", "_"))


def _encode_image(path: Path) -> dict[str, Any]:
    """Encode a PNG as base64 for the Anthropic vision API."""
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": data,
        },
    }


def _build_user_content(
    reconciled: ReconciledVerdict,
    screenshots: list[tuple[Path, str]],
) -> list[dict[str, Any]]:
    """Build multi-part message content: screenshots + labels + XML envelope."""
    content: list[dict[str, Any]] = []

    # Interleave image + label so the model can distinguish them.
    for path, label in screenshots:
        content.append(
            {
                "type": "text",
                "text": f"[Screenshot: {label}]",
            }
        )
        content.append(_encode_image(path))

    # Envelope with heuristics.
    escape = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})
    envelope = [
        f"<cluster_id>{reconciled.cluster_id.translate(escape)}</cluster_id>",
        "<baseline_heuristics>",
    ]
    for v in reconciled.ranked_violations:
        envelope.append(
            f'  <h slug="{v.heuristic.translate(escape)}" '
            f'baseline_severity="{v.severity}">'
            f"{v.violation.translate(escape)}"
            f"</h>"
        )
    envelope.append("</baseline_heuristics>")
    envelope.append(
        "<task>For each heuristic above, verify against the "
        "screenshots and emit grounded evidence per the output format "
        "in the system prompt.</task>"
    )

    content.append({"type": "text", "text": "\n".join(envelope)})
    return content


def _extract_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise RuntimeError(f"no JSON object in response: {text!r}")
    return json.loads(m.group(0))


def _validate_payload(
    payload: dict[str, Any], baseline_slugs: set[str]
) -> None:
    if "grounded_evidence" not in payload:
        raise RuntimeError("missing 'grounded_evidence' key")
    ge = payload["grounded_evidence"]
    if not isinstance(ge, dict):
        raise RuntimeError("'grounded_evidence' must be dict")
    missing = baseline_slugs - set(ge.keys())
    extra = set(ge.keys()) - baseline_slugs
    if missing:
        raise RuntimeError(f"grounded_evidence missing heuristics: {sorted(missing)}")
    if extra:
        raise RuntimeError(f"grounded_evidence extra heuristics: {sorted(extra)}")
    for slug, entry in ge.items():
        if entry.get("confirmed") not in {"confirmed", "partial", "refuted"}:
            raise RuntimeError(
                f"{slug}: 'confirmed' must be one of confirmed|partial|refuted, "
                f"got {entry.get('confirmed')!r}"
            )
        sev = entry.get("adjusted_severity")
        if sev not in {0, 3, 5, 7, 9}:
            raise RuntimeError(
                f"{slug}: 'adjusted_severity' must be in {{0,3,5,7,9}}, got {sev!r}"
            )
    if "summary" not in payload or not isinstance(payload["summary"], str):
        raise RuntimeError("'summary' must be non-empty str")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify-on-product — grounded-evidence hook that checks "
            "L5 reconciled heuristics against real product screenshots."
        )
    )
    parser.add_argument("--reconciled", type=Path, default=DEFAULT_RECONCILED)
    parser.add_argument(
        "--screenshots-dir", type=Path, default=DEFAULT_SCREENSHOTS_DIR
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)

    # --- Load L5 reconciled verdict --------------------------------
    recon_map = load_reconciled_verdicts(args.reconciled)
    if len(recon_map) != 1:
        raise RuntimeError(
            f"expected one reconciled verdict in {args.reconciled}, got {len(recon_map)}"
        )
    cluster_id, reconciled = next(iter(recon_map.items()))
    baseline_slugs = {v.heuristic for v in reconciled.ranked_violations}

    # --- Gather screenshots ----------------------------------------
    ss_dir = args.screenshots_dir
    screenshots = [
        (ss_dir / "energy_manage.png", "energy_manage — dedicated energy surface with 22h 31m regen timer, Super upsell, Recharge (500 gems), Mini charge"),
        (ss_dir / "out_of_energy_home.png", "out_of_energy_home — blocking modal from home/between-lessons, Super with checkmark default, Recharge (450 gems), Quit lesson muted"),
        (ss_dir / "out_of_energy_mid_lesson.png", "out_of_energy_mid_lesson — modal blocks mid-lesson ('Translate this sentence' visible above), 'TRY 1 WEEK FOR FREE' primary, 'LOSE XP' as punitive alternative"),
    ]
    for path, _ in screenshots:
        if not path.exists():
            raise RuntimeError(f"screenshot missing: {path}")

    # --- Build + dispatch VLM call --------------------------------
    content = _build_user_content(reconciled, screenshots)

    client = anthropic.Anthropic()
    kwargs: dict[str, Any] = {
        "model": args.model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": content}],
    }
    if not _omits_sampling_params(args.model):
        kwargs["temperature"] = 0.0

    print(
        f"[verify] cluster={cluster_id} model={args.model} "
        f"baseline_heuristics={len(baseline_slugs)} "
        f"screenshots={len(screenshots)}",
        flush=True,
    )
    message = client.messages.create(**kwargs)
    response_chunks = [
        block.text for block in message.content
        if getattr(block, "type", None) == "text"
    ]
    response_text = "".join(response_chunks)

    payload = _extract_json(response_text)
    _validate_payload(payload, baseline_slugs)

    # --- Write outputs --------------------------------------------
    args.out_dir.mkdir(parents=True, exist_ok=True)
    short = _short_model(args.model)
    cluster_stem = cluster_id.replace("_", "")
    suffix = f"{cluster_stem}_{short}"

    json_path = args.out_dir / f"verify_on_product_{suffix}.json"
    md_path = args.out_dir / f"verify_on_product_{suffix}.md"
    prov_path = args.out_dir / f"verify_on_product_{suffix}.provenance.json"

    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # --- Markdown artifact with embedded screenshot references ----
    lines: list[str] = [
        f"# {cluster_id} — verify-on-product grounded evidence",
        "",
        f"Model: `{args.model}` · Cluster: `{cluster_id}` · "
        f"Baseline heuristics: {len(baseline_slugs)}",
        "",
        "## Screenshots audited",
        "",
    ]
    for path, label in screenshots:
        rel = path.relative_to(_REPO_ROOT)
        lines.append(f"- `{rel}` — {label}")
    lines += [
        "",
        "## Baseline heuristics (from L5 reconciled) vs grounded evidence",
        "",
        "| Heuristic | Baseline sev | Adjusted sev | Verdict | Evidence |",
        "|---|---|---|---|---|",
    ]
    for v in reconciled.ranked_violations:
        entry = payload["grounded_evidence"][v.heuristic]
        ev = entry["evidence"].replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| `{v.heuristic}` | {v.severity} | "
            f"{entry['adjusted_severity']} | "
            f"{entry['confirmed']} | {ev} |"
        )
    lines += [
        "",
        "## Per-heuristic reasoning",
        "",
    ]
    for v in reconciled.ranked_violations:
        entry = payload["grounded_evidence"][v.heuristic]
        lines += [
            f"### `{v.heuristic}`",
            f"- **Verdict:** {entry['confirmed']}",
            f"- **Baseline severity:** {v.severity} → "
            f"**adjusted:** {entry['adjusted_severity']}",
            f"- **Evidence:** {entry['evidence']}",
            f"- **Reasoning:** {entry['reasoning']}",
            "",
        ]
    lines += [
        "## Summary",
        "",
        payload["summary"],
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")

    # --- Provenance -----------------------------------------------
    baseline_sum = sum(int(v.severity) for v in reconciled.ranked_violations)
    adjusted_sum = sum(
        int(payload["grounded_evidence"][v.heuristic]["adjusted_severity"])
        for v in reconciled.ranked_violations
    )
    verdict_counts: dict[str, int] = {}
    for entry in payload["grounded_evidence"].values():
        k = entry["confirmed"]
        verdict_counts[k] = verdict_counts.get(k, 0) + 1

    provenance = {
        "schema_version": 1,
        "layer": "verify_on_product",
        "cluster_id": cluster_id,
        "model": args.model,
        "baseline_heuristic_count": len(baseline_slugs),
        "baseline_severity_sum": baseline_sum,
        "adjusted_severity_sum": adjusted_sum,
        "verdict_counts": verdict_counts,
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
        "screenshots": [
            {
                "path": str(path.relative_to(_REPO_ROOT)),
                "sha256": _sha256(path),
            }
            for path, _ in screenshots
        ],
        "reconciled_sha256": _sha256(args.reconciled),
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    prov_path.write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(
        f"[verify] baseline sum={baseline_sum} adjusted sum={adjusted_sum} "
        f"verdicts={verdict_counts}",
        flush=True,
    )
    print(f"[verify] json  → {json_path}", flush=True)
    print(f"[verify] md    → {md_path}", flush=True)
    print(f"[verify] prov  → {prov_path}", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
