"""Build the OneDrive folder tree for the Copilot Studio test.

Run from the repo root (needs the study data in data/ and the backfilled
copy from scripts/run_backfill_evidence_chain_study.sh):

    uv run python copilot-studio/narzedzia/przygotuj_onedrive.py <out_dir>

Writes <out_dir>/AuditableDesign/ ready to upload to OneDrive:

  konfiguracja/   konfiguracja.json, przyklady/run_request_*.json
  start/          empty; dropping run_request.json here starts the flow
  wejscie/
    badanie_A/    klastry.json (the six grid clusters, as the study's L4
                  saw them, with representative_review_ids),
                  klastry_L5_<model>.json (the same clusters as L5–L8 saw
                  them in that model's branch), artefakty/*.png,
                  referencja/ (study outputs for Opus 4.6 and Sonnet 4.6)
    badanie_B/    wypowiedzi.json (the 600-review corpus as L0 input)
  przebiegi/      empty; the flow writes one folder per run here
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "derived"
M = ROOT / "data" / "derived_evchain" / "data" / "derived"
GRID = ["cluster_00", "cluster_01", "cluster_06", "cluster_11", "cluster_12", "cluster_13"]
LENSES = ["usability_fundamentals", "accessibility", "decision_psychology", "business_alignment", "interaction_design", "ux_architecture"]
# Models available in Copilot Studio that the study also ran.
REF_BRANCHES = {"opus46": "Claude Opus 4.6", "sonnet46": "Claude Sonnet 4.6"}


def jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def dump(p: Path, obj: object) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def main(out_dir: Path) -> None:
    base = out_dir / "AuditableDesign"
    if base.exists():
        shutil.rmtree(base)

    # --- konfiguracja ----------------------------------------------------
    dump(base / "konfiguracja" / "konfiguracja.json", {
        "soczewki": ["audit-usability-fundamentals", "audit-accessibility", "audit-decision-psychology",
                     "audit-business-alignment", "audit-interaction-design", "audit-ux-architecture"],
        "l3_min_rozmiar_klastra": 5,
        "l6_liczba_przebiegow": 2,
        "l8_weryfikator": "tchebycheff",
        "l8_max_iteracji": 5,
        "uwaga": "Model ustawia się w każdym prompcie w Copilot Studio; wpisz go w run_request.json (pole model_opis), żeby trafił do dziennika.",
    })
    dump(base / "konfiguracja" / "przyklady" / "run_request_A.json", {
        "run_id": "A-opus46-001",
        "tryb": "A_od_klastrow",
        "zestaw": "badanie_A",
        "klastry": ["cluster_11"],
        "klastry_l5": "klastry_L5_opus46.json",
        "model_opis": "Claude Opus 4.6 we wszystkich promptach",
    })
    dump(base / "konfiguracja" / "przyklady" / "run_request_B.json", {
        "run_id": "B-opus46-001",
        "tryb": "B_od_wypowiedzi",
        "zestaw": "badanie_B",
        "limit_wypowiedzi": 60,
        "model_opis": "Claude Opus 4.6 we wszystkich promptach",
    })
    (base / "start").mkdir(parents=True)
    (base / "przebiegi").mkdir(parents=True)
    (base / "start" / "CZYTAJ.txt").write_text(
        "Wrzuć tu plik run_request.json (wzór: konfiguracja/przyklady). Flow AD_Przebieg startuje po pojawieniu się pliku.\n",
        encoding="utf-8",
    )

    # --- badanie A: the six grid clusters as L4 saw them -----------------
    backfilled = {c["cluster_id"]: c for c in jsonl(M / "l3_clusters" / "shared_l2opus47" / "l3_clusters.jsonl")}
    clusters = []
    for cid in GRID:
        stem = cid.replace("_", "_")
        row = jsonl(D / "l4_audit" / "audit_interaction_design" / "shared_l2opus47" / f"audit_interaction_design_input_{stem}.jsonl")[0]
        row["representative_review_ids"] = backfilled[cid]["representative_review_ids"]
        clusters.append(row)
    a = base / "wejscie" / "badanie_A"
    dump(a / "klastry.json", clusters)
    # In the study L5–L8 saw a different cluster object than L4: each
    # branch's own L3b label and no ui_context/screenshot_ref. These files
    # let the flow reproduce that (run_request field "klastry_l5").
    for branch in REF_BRANCHES:
        dump(a / f"klastry_L5_{branch}.json", jsonl(M / "l5_reconcile" / "shared_l2opus47" / f"l3b_filtered_{branch}.jsonl"))
    for png in sorted((ROOT / "data" / "raw" / "duolingo_screenshots").glob("*.png")):
        (a / "artefakty").mkdir(parents=True, exist_ok=True)
        shutil.copy(png, a / "artefakty" / png.name)
    # Captions for verify-on-product, in the order and wording hard-coded
    # in scripts/verify_on_product.py; `path` is the text the study put in
    # the message, `plik` is the file in artefakty/.
    dump(a / "artefakty" / "opisy.json", [
        {"plik": "energy_manage.png", "path": "data/raw/duolingo_screenshots/energy_manage.png",
         "label": "energy_manage — dedicated energy surface with 22h 31m regen timer, Super upsell, Recharge (500 gems), Mini charge"},
        {"plik": "out_of_energy_home.png", "path": "data/raw/duolingo_screenshots/out_of_energy_home.png",
         "label": "out_of_energy_home — blocking modal from home/between-lessons, Super with checkmark default, Recharge (450 gems), Quit lesson muted"},
        {"plik": "out_of_energy_mid_lesson.png", "path": "data/raw/duolingo_screenshots/out_of_energy_mid_lesson.png",
         "label": "out_of_energy_mid_lesson — modal blocks mid-lesson ('Translate this sentence' visible above), 'TRY 1 WEEK FOR FREE' primary, 'LOSE XP' as punitive alternative"},
    ])

    # --- reference outputs from the study --------------------------------
    for branch in REF_BRANCHES:
        ref = a / "referencja" / branch
        l4 = []
        for lens in LENSES:
            for cid in GRID:
                p = M / "l4_audit" / f"audit_{lens}" / "shared_l2opus47" / f"l4_verdicts_audit_{lens}_{cid.replace('_', '')}_{branch}_multimodal.jsonl"
                if p.exists():
                    l4 += jsonl(p)
        dump(ref / "L4_werdykty.json", l4)
        dump(ref / "L5_uzgodnienie.json", jsonl(M / "l5_reconcile" / "shared_l2opus47" / f"l5_reconciled_{branch}.jsonl"))
        for name, p in [
            ("L6_priorytet.json", D / "l6_weight" / "shared_l2opus47" / f"l6_priority_{branch}.jsonl"),
            ("L7_decyzje.json", D / "l7_decide" / "shared_l2opus47" / f"l7_design_decisions_{branch}.jsonl"),
            ("L8_iteracje_cluster11.json", D / "l8_loop" / "shared_l2opus47" / f"l8_loop_iterations_cluster11_{branch}_tchebycheff.jsonl"),
        ]:
            if p.exists():
                dump(ref / name, jsonl(p))
        v = D / "verify_on_product" / "shared_l2opus47" / f"verify_on_product_cluster11_{branch}.json"
        if v.exists():
            shutil.copy(v, ref / "weryfikacja_cluster11.json")
        b = M / "design_brief" / "shared_l2opus47" / f"design_brief_cluster11_{branch}.md"
        if b.exists():
            shutil.copy(b, ref / "brief_cluster11.md")

    # --- badanie B: the corpus as L0 input -------------------------------
    corpus = jsonl(ROOT / "data" / "raw" / "corpus.jsonl")
    dump(base / "wejscie" / "badanie_B" / "wypowiedzi.json", {
        "sources": [{
            "source_type": "app_review",
            "items": [{"id": r["review_id"], "text": r["text"], "rating": r["rating"], "date": r["timestamp_utc"]} for r in corpus],
        }]
    })

    files = [p for p in base.rglob("*") if p.is_file()]
    print(f"wrote {len(files)} files under {base}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "copilot-studio" / "_onedrive_build")
