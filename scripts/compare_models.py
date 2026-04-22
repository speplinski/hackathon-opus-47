"""Compare two L1 classifier runs (e.g. Sonnet vs Opus) against human gold.

Intent
------
Decide which model to use for the full N=600 L1 classification run by
running both on the N=20 stratified pilot and comparing:

1. Each model vs ``l1_gold.csv`` — accuracy on ``is_ux_relevant``, mean
   Jaccard on ``rubric_tags``, confidence discrimination.
2. The two models vs each other — Cohen's kappa on ``is_ux_relevant``
   (inter-model agreement floor), distribution of Jaccard on
   ``rubric_tags``.
3. Disagreement mining — list the review_ids where the models
   disagreed, with both labels side by side, so an operator can
   eyeball where the two models draw different lines.

Jaccard convention: the empty-∩-empty case yields 1.0 (two labelers
agreeing "no tags" is perfect agreement, not undefined).

Cohen's kappa is computed from the 2x2 confusion matrix of
``is_ux_relevant`` labels. Kappa corrects observed agreement for
chance — useful because is_ux_relevant is imbalanced (gold is
11/20=55% positive, so naive agreement rates inflate easily).

Usage
-----
::

    python scripts/compare_models.py \\
        --sonnet data/derived/l1_sonnet.jsonl \\
        --opus data/derived/l1_opus.jsonl \\
        --gold data/eval/l1_gold.csv

Prints a plain-text report to stdout. No files written; the raw JSONL
artifacts are already on disk and the report is short enough to paste
into a decision memo.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GoldRow:
    review_id: str
    is_ux: bool
    tags: frozenset[str]


@dataclass(frozen=True)
class PredRow:
    review_id: str
    is_ux: bool
    confidence: float
    tags: frozenset[str]


def load_gold(path: Path) -> dict[str, GoldRow]:
    out: dict[str, GoldRow] = {}
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tags_str = (row["rubric_tags"] or "").strip()
            tags = frozenset(t for t in tags_str.split(",") if t)
            out[row["review_id"]] = GoldRow(
                review_id=row["review_id"],
                is_ux=row["is_ux_relevant"] == "1",
                tags=tags,
            )
    return out


def load_preds(path: Path) -> dict[str, PredRow]:
    out: dict[str, PredRow] = {}
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            out[d["review_id"]] = PredRow(
                review_id=d["review_id"],
                is_ux=bool(d["is_ux_relevant"]),
                confidence=float(d["classifier_confidence"]),
                tags=frozenset(d["rubric_tags"]),
            )
    return out


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard with the empty∩empty → 1.0 convention."""
    if not a and not b:
        return 1.0
    u = a | b
    if not u:  # belt-and-braces
        return 1.0
    return len(a & b) / len(u)


def cohens_kappa(pairs: list[tuple[bool, bool]]) -> float:
    """Cohen's kappa on a 2-class labelling.

    Returns 1.0 for perfect agreement, 0.0 for chance-level, negative
    for worse-than-chance. Undefined (returns 1.0) if both raters never
    produced any variance — there is no disagreement to correct for.
    """
    if not pairs:
        return 0.0
    n = len(pairs)
    po = sum(1 for a, b in pairs if a == b) / n
    p_a_true = sum(1 for a, _ in pairs if a) / n
    p_b_true = sum(1 for _, b in pairs if b) / n
    pe = p_a_true * p_b_true + (1 - p_a_true) * (1 - p_b_true)
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def confidence_discrimination(
    preds: dict[str, PredRow], gold: dict[str, GoldRow]
) -> tuple[float, float, float]:
    """Mean confidence on correct vs incorrect ``is_ux_relevant`` predictions.

    Returns ``(mean_correct, mean_incorrect, delta)``. Positive delta
    means the model is more confident when it is right — a minimal
    calibration signal. We don't demand delta > some threshold, just
    that it is positive (see the triad definition in the L1 pilot plan).
    """
    correct: list[float] = []
    incorrect: list[float] = []
    for rid, p in preds.items():
        if rid not in gold:
            continue
        if p.is_ux == gold[rid].is_ux:
            correct.append(p.confidence)
        else:
            incorrect.append(p.confidence)
    mc = sum(correct) / len(correct) if correct else float("nan")
    mi = sum(incorrect) / len(incorrect) if incorrect else float("nan")
    return mc, mi, mc - mi


def model_vs_gold(
    name: str, preds: dict[str, PredRow], gold: dict[str, GoldRow]
) -> dict[str, float]:
    """Accuracy on is_ux_relevant, mean Jaccard on rubric_tags, confidence delta."""
    shared = set(preds) & set(gold)
    if not shared:
        raise ValueError(f"{name}: no review_ids overlap with gold")

    correct_ux = sum(1 for rid in shared if preds[rid].is_ux == gold[rid].is_ux)
    acc = correct_ux / len(shared)

    jaccs = [jaccard(preds[rid].tags, gold[rid].tags) for rid in shared]
    mean_jacc = sum(jaccs) / len(jaccs)

    mc, mi, delta = confidence_discrimination(preds, gold)

    return {
        "n": float(len(shared)),
        "accuracy": acc,
        "mean_jaccard": mean_jacc,
        "confidence_correct": mc,
        "confidence_incorrect": mi,
        "confidence_delta": delta,
    }


def inter_model(
    a: dict[str, PredRow], b: dict[str, PredRow]
) -> tuple[float, float, list[str]]:
    """Cohen's kappa on is_ux, mean Jaccard on tags, ids where they disagree."""
    shared = sorted(set(a) & set(b))
    if not shared:
        raise ValueError("no review_ids overlap between the two model outputs")

    pairs = [(a[rid].is_ux, b[rid].is_ux) for rid in shared]
    kappa = cohens_kappa(pairs)

    jaccs = [jaccard(a[rid].tags, b[rid].tags) for rid in shared]
    mean_jacc = sum(jaccs) / len(jaccs)

    disagree = [
        rid for rid in shared
        if a[rid].is_ux != b[rid].is_ux or a[rid].tags != b[rid].tags
    ]
    return kappa, mean_jacc, disagree


def _fmt(x: float) -> str:
    return f"{x:.3f}" if x == x else "n/a"  # nan-safe


def print_report(
    sonnet: dict[str, PredRow],
    opus: dict[str, PredRow],
    gold: dict[str, GoldRow],
) -> None:
    print("=" * 72)
    print("L1 classifier — Sonnet vs Opus cross-check")
    print("=" * 72)

    for name, preds in (("Sonnet", sonnet), ("Opus", opus)):
        m = model_vs_gold(name, preds, gold)
        print(f"\n[{name}] vs gold (n={int(m['n'])})")
        print(f"  is_ux_relevant accuracy : {_fmt(m['accuracy'])}   (triad: >=0.85)")
        print(f"  rubric_tags mean Jaccard: {_fmt(m['mean_jaccard'])}   (triad: >=0.60)")
        print(
            f"  confidence delta        : {_fmt(m['confidence_delta'])}   "
            f"(triad: >0; correct={_fmt(m['confidence_correct'])} "
            f"incorrect={_fmt(m['confidence_incorrect'])})"
        )

    kappa, mean_jacc, disagree = inter_model(sonnet, opus)
    print("\n[Sonnet vs Opus] inter-model agreement")
    print(f"  Cohen's kappa on is_ux : {_fmt(kappa)}")
    print(f"  mean Jaccard on tags   : {_fmt(mean_jacc)}")
    print(f"  disagreements          : {len(disagree)} / {len(set(sonnet) & set(opus))}")

    if disagree:
        print("\nDisagreements (review_id | sonnet → opus | gold):")
        for rid in disagree:
            s, o = sonnet[rid], opus[rid]
            g = gold.get(rid)
            gstr = (
                f"gold={int(g.is_ux)} tags={sorted(g.tags) or '[]'}"
                if g
                else "gold=<missing>"
            )
            print(
                f"  {rid[:12]} | "
                f"s: ux={int(s.is_ux)} conf={s.confidence:.2f} tags={sorted(s.tags) or '[]'} | "
                f"o: ux={int(o.is_ux)} conf={o.confidence:.2f} tags={sorted(o.tags) or '[]'} | "
                f"{gstr}"
            )

    print("\n" + "=" * 72)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sonnet", type=Path, required=True, help="L1 output from Sonnet run")
    p.add_argument("--opus", type=Path, required=True, help="L1 output from Opus run")
    p.add_argument(
        "--gold",
        type=Path,
        default=Path("data/eval/l1_gold.csv"),
        help="Human gold labels (default: data/eval/l1_gold.csv)",
    )
    args = p.parse_args(argv)

    gold = load_gold(args.gold)
    sonnet = load_preds(args.sonnet)
    opus = load_preds(args.opus)

    missing_sonnet = set(gold) - set(sonnet)
    missing_opus = set(gold) - set(opus)
    if missing_sonnet or missing_opus:
        print(
            f"WARNING: gold has {len(gold)} reviews, "
            f"sonnet missing {len(missing_sonnet)}, opus missing {len(missing_opus)}",
            file=sys.stderr,
        )

    print_report(sonnet, opus, gold)
    return 0


if __name__ == "__main__":
    sys.exit(main())
