"""Build L4 audit input JSONL files for the shared_l2opus47 matched grid.

Reads the opus47 L3b labeled clusters (data/derived/l3b_labeled_clusters/
shared_l2opus47/l3b_labeled_clusters_opus47.jsonl), augments each target
cluster row with lens-agnostic ``ui_context`` and a real-product
``screenshot_ref`` pointing at ``data/raw/duolingo_screenshots/*.png``,
and writes per-(lens, cluster) input files the L4 smoke scripts can
consume unchanged.

Policy decisions (from the 2026-04-24 walkthrough session):

* ``html`` is set to None. The existing L4 prompts treat ``<html>`` as
  optional (``build_user_message`` renders it iff non-None). Text-mode
  runs carry the cluster's label + representative_quotes which already
  describe what users complain about; crafting a synthetic HTML mockup
  would duplicate that signal and drift from the real pixels the
  image-mode run actually audits.

* ``screenshot_ref`` is always set, even for text-mode runs. The smoke
  chooses whether to attach the PNG based on ``--modality`` — the XML
  reference without an attached image is legible to the model as "the
  surface is at this path" and stays consistent across modalities for
  the matched-grid diff.

* Only 6 of the 14 L3 clusters are covered here: the ones with a
  canonical real screenshot in data/raw/duolingo_screenshots/. The
  remaining 8 (chess, AI, ads, lesson-completion, etc.) would need
  additional captured surfaces — a follow-up for scale-up, not POC.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

L3B_OPUS47 = (
    REPO_ROOT
    / "data/derived/l3b_labeled_clusters/shared_l2opus47"
    / "l3b_labeled_clusters_opus47.jsonl"
)
SCREENSHOTS_DIR = REPO_ROOT / "data/raw/duolingo_screenshots"
L4_ROOT = REPO_ROOT / "data/derived/l4_audit"

LENSES = [
    "audit_accessibility",
    "audit_business_alignment",
    "audit_decision_psychology",
    "audit_interaction_design",
    "audit_usability_fundamentals",
    "audit_ux_architecture",
]

# Per-cluster UI binding: (screenshot filename, ui_context prose).
# ui_context is grounded in what the actual PNG shows — written once,
# shared across all 6 lenses (consistent with the legacy cluster_02
# input files which used a single ui_context across 4 of 6 lenses).
_UI_ENERGY_MANAGE = (
    "Duolingo mobile app — dedicated energy management surface, "
    "accessed from the top-nav energy indicator. Dark theme. Shows a "
    "'7 / 30' energy meter with a '22H 31M' regeneration timer in the "
    "header. Below: a gradient 'SUPER — Unlimited — GET SUPER' card "
    "(vivid pink/purple/green), a low-contrast grey 'Recharge' row "
    "('30 | Recharge | 500 gems'), and a pink-accented 'Mini charge' "
    "row ('5 | Mini charge | WATCH AD'). Non-blocking surface — the "
    "user can back out without transacting."
)
_UI_PAYWALL_HOME = (
    "Duolingo mobile app between-lessons blocker. Full-viewport "
    "'You ran out of energy!' modal that appears when the user tries "
    "to start a new lesson with zero energy. Two offer cards: a large "
    "pink-bordered SUPER card with a ✓ checkmark decoration in the "
    "top-right ('Unlimited / TRY FREE'), and a smaller Recharge card "
    "('pink battery / Recharge / 450 gems'). Primary blue CTA "
    "'TRY 1 WEEK FOR FREE' below. Non-paid exit: 'QUIT LESSON' in "
    "muted blue text at the bottom."
)
_UI_PAYWALL_MID_LESSON = (
    "Duolingo mobile app mid-lesson blocker. A 'Translate this "
    "sentence' lesson prompt is visible above, indicating the user "
    "was already answering a question when energy depleted. "
    "Full-viewport 'You ran out of energy!' modal. Two offer cards "
    "(SUPER with ✓ / Recharge 450 gems). Primary blue CTA 'TRY 1 "
    "WEEK FOR FREE'. Non-paid exit at bottom: 'LOSE XP' in muted "
    "blue text — framing the free exit as punitive. Energy counter "
    "at top-right shows '8'."
)

CLUSTER_CONFIG: dict[str, tuple[str, str]] = {
    # Energy-management clusters — dedicated surface, non-blocking.
    "cluster_00": ("energy_manage.png", _UI_ENERGY_MANAGE),
    "cluster_12": ("energy_manage.png", _UI_ENERGY_MANAGE),
    "cluster_13": ("energy_manage.png", _UI_ENERGY_MANAGE),
    # Paywall upsell clusters — between-lessons blocker.
    "cluster_01": ("out_of_energy_home.png", _UI_PAYWALL_HOME),
    "cluster_06": ("out_of_energy_home.png", _UI_PAYWALL_HOME),
    # Streak/XP loss — mid-lesson blocker (LOSE XP visible).
    "cluster_11": ("out_of_energy_mid_lesson.png", _UI_PAYWALL_MID_LESSON),
}


def main() -> int:
    if not L3B_OPUS47.exists():
        print(f"error: L3b opus47 output missing at {L3B_OPUS47}", file=sys.stderr)
        return 1

    # Verify screenshots exist.
    missing = [
        shot
        for (shot, _) in CLUSTER_CONFIG.values()
        if not (SCREENSHOTS_DIR / shot).exists()
    ]
    if missing:
        print(f"error: missing screenshots: {sorted(set(missing))}", file=sys.stderr)
        return 1

    # Load L3b opus47 clusters by id.
    by_id: dict[str, dict] = {}
    with L3B_OPUS47.open() as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            by_id[d["cluster_id"]] = d

    unknown = [cid for cid in CLUSTER_CONFIG if cid not in by_id]
    if unknown:
        print(f"error: clusters not in L3b output: {unknown}", file=sys.stderr)
        return 1

    written = 0
    for cid, (shot, uicx) in CLUSTER_CONFIG.items():
        cluster = by_id[cid]
        screenshot_rel = f"data/raw/duolingo_screenshots/{shot}"
        row = {
            "cluster_id": cid,
            "label": cluster["label"],
            "member_review_ids": cluster["member_review_ids"],
            "representative_quotes": cluster["representative_quotes"],
            "centroid_vector_ref": cluster.get("centroid_vector_ref"),
            "ui_context": uicx,
            "html": None,
            "screenshot_ref": screenshot_rel,
        }
        serialized = json.dumps(row, ensure_ascii=False) + "\n"
        for lens in LENSES:
            out_dir = L4_ROOT / lens / "shared_l2opus47"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{lens}_input_{cid}.jsonl"
            out_path.write_text(serialized, encoding="utf-8")
            written += 1

    print(
        f"[build-l4-inputs] wrote {written} input files "
        f"({len(CLUSTER_CONFIG)} clusters × {len(LENSES)} lenses) "
        f"under {L4_ROOT}/*/shared_l2opus47/"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
