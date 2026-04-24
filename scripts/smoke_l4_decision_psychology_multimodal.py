"""One-shot smoke for the L4 decision-psychology audit — text or multimodal.

Companion to the production ``l4_audit_decision_psychology`` module.
Takes one enriched cluster, calls the Anthropic SDK directly with
either a plain text user turn or a text+image user turn, parses the
response through the same parser the module uses, and writes verdicts
/ native / provenance next to the module's own outputs. Zero cache
interaction.

The script covers both modalities so a matched-model eval (opus46 /
sonnet46 / opus47 × text / multimodal) is one bash loop away without
two divergent smoke entry points. Structurally identical to
``smoke_l4_accessibility_multimodal.py`` — only the module import,
provenance shape (intent histogram + mechanism counts instead of
WCAG level histogram + WCAG ref counts) and the default input /
screenshot / output-dir differ.

Output
------
Mirrors the module's native output contract, with a per-(model,
modality) suffix so all six runs in a matched eval coexist:

* text:      ``…_cluster02_<modelshort>.{jsonl,native.jsonl,provenance.json}``
* multimodal: ``…_cluster02_<modelshort>_multimodal.{jsonl,…}``

Model-short mapping: claude-opus-4-6 → opus46, claude-sonnet-4-6 →
sonnet46, claude-opus-4-7 → opus47, claude-haiku-4-5 → haiku45.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import anthropic

# Make `src/` importable without requiring `uv run` wrapping.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from auditable_design.claude_client import _omits_sampling_params  # noqa: E402
from auditable_design.layers.l4_audit import (  # noqa: E402
    AuditParseError,
    _fallback_native,
    _verdict_id,
)
from auditable_design.layers.l4_audit_decision_psychology import (  # noqa: E402
    DIMENSION_KEYS,
    MAX_TOKENS,
    SKILL_ID,
    SYSTEM_PROMPT,
    TEMPERATURE,
    VALID_INTENTS,
    _build_heuristic_violations,
    build_user_message,
    parse_audit_response,
    skill_hash,
)
from auditable_design.schemas import AuditVerdict, InsightCluster  # noqa: E402

DEFAULT_INPUT = (
    _REPO_ROOT
    / "data/derived/l4_audit/audit_decision_psychology/audit_decision_psychology_input.jsonl"
)
DEFAULT_SCREENSHOT = _REPO_ROOT / "data/artifacts/ui/duolingo_streak_modal.png"
DEFAULT_OUT_DIR = _REPO_ROOT / "data/derived/l4_audit/audit_decision_psychology"
DEFAULT_MODEL = "claude-opus-4-7"


def _load_cluster(path: Path) -> InsightCluster:
    line = path.read_text(encoding="utf-8").strip()
    if not line:
        raise RuntimeError(f"empty input: {path}")
    first_line = line.splitlines()[0]
    return InsightCluster.model_validate_json(first_line)


_MODEL_SHORT = {
    "claude-opus-4-6": "opus46",
    "claude-sonnet-4-6": "sonnet46",
    "claude-opus-4-7": "opus47",
    "claude-haiku-4-5": "haiku45",
}


def _short_model_name(model: str) -> str:
    """Project the full model id to the compact label used in filenames.

    Dated variants (``claude-opus-4-7-20260416`` etc.) collapse to the
    same short name as the base — the dated suffix is noise for the
    purposes of a matched-model eval output file layout. Unknown models
    fall back to their full id, slash-stripped.
    """
    for full, short in _MODEL_SHORT.items():
        if model.startswith(full):
            return short
    return model.replace("/", "_")


def _load_screenshot(path: Path) -> tuple[str, str]:
    """Return (media_type, base64_data) for the given image."""
    if not path.exists():
        raise FileNotFoundError(
            f"screenshot not found at {path} — pass --screenshot to override"
        )
    suffix = path.suffix.lower()
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix)
    if media_type is None:
        raise ValueError(
            f"unsupported image extension {suffix!r} at {path}; "
            f"expected one of .png/.jpg/.webp/.gif"
        )
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return media_type, data


def _run(
    *,
    cluster: InsightCluster,
    media_type: str | None,
    image_b64: str | None,
    model: str,
    include_image: bool,
) -> tuple[anthropic.types.Message, str]:
    """One Claude call. Returns (raw Message, response text).

    When ``include_image`` is True, ``media_type`` and ``image_b64``
    must both be provided and the user turn becomes a two-block
    content (image first, then the ``<cluster>`` XML text). When
    False, the user turn is a plain string — byte-identical to what
    the production text-only module builds, so this path is a
    faithful stand-in when the module's cache is cold or when we
    want to compare text-only against multimodal without involving
    the replay log.
    """
    client = anthropic.Anthropic()
    user_text = build_user_message(cluster)

    if include_image:
        if media_type is None or image_b64 is None:
            raise ValueError("include_image=True requires media_type and image_b64")
        # Image first, then text — Anthropic guidance is to put the
        # image before the instruction that references it.
        user_content: list[dict] | str = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": image_b64,
                },
            },
            {"type": "text", "text": user_text},
        ]
    else:
        # Plain string content — matches ``claude_client._dispatch``'s
        # shape exactly (``"content": user``), so the text path is a
        # faithful stand-in for the production module's call.
        user_content = user_text

    kwargs: dict[str, object] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
    }
    # Opus 4.7 rejects `temperature` with 400. The production client
    # (``claude_client._dispatch``) applies the same gate via
    # ``_omits_sampling_params``; we reuse it so this smoke mirrors
    # real behaviour exactly.
    if not _omits_sampling_params(model):
        kwargs["temperature"] = TEMPERATURE
    message = client.messages.create(**kwargs)
    # Message.content is a list of blocks; the audit prompt elicits a
    # single text block. Concatenate defensively.
    chunks: list[str] = []
    for block in message.content:
        if getattr(block, "type", None) == "text":
            chunks.append(block.text)
    return message, "".join(chunks)


def _build_provenance(
    *,
    cluster_id: str,
    model: str,
    modality: str,
    payload: dict | None,
    parse_error: str | None,
    input_tokens: int,
    output_tokens: int,
    sh: str,
    media_type: str | None,
    screenshot_bytes: int | None,
) -> dict:
    """Mirror of the production ``build_provenance`` for one-cluster smoke.

    Shape parallels the full-module provenance so reviewers can diff
    smoke vs. prod without adapter code. Kahneman-specific: per-intent
    histogram + top-mechanism counts (sorted by count then mechanism
    name for deterministic diffs), instead of WCAG fields.
    """
    audited = 1 if payload is not None else 0
    fallback = 1 if payload is None else 0

    dim_totals = {k: 0 for k in DIMENSION_KEYS}
    findings_count = 0
    severity_hist: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}
    intent_hist: dict[str, int] = {v: 0 for v in VALID_INTENTS}
    mech_counts: Counter[str] = Counter()

    if payload is not None:
        for k in DIMENSION_KEYS:
            dim_totals[k] += int(payload["dimension_scores"][k])
        for f in payload["findings"]:
            findings_count += 1
            sev = int(f["severity"])
            severity_hist[sev] = severity_hist.get(sev, 0) + 1
            intent = f["intent"]
            intent_hist[intent] = intent_hist.get(intent, 0) + 1
            mech_counts[f["mechanism"]] += 1

    top_mechanisms = sorted(
        mech_counts.items(),
        key=lambda kv: (-kv[1], kv[0]),
    )

    return {
        "skill_id": SKILL_ID,
        "model": model,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "mode": f"{modality}_direct_sdk",
        "modality": modality,
        "cluster_count": 1,
        "audited_count": audited,
        "fallback_count": fallback,
        "transport_failure_count": 0,
        "dimension_score_totals": dim_totals,
        "findings_count": findings_count,
        "nielsen_severity_histogram": severity_hist,
        "intent_histogram": intent_hist,
        "mechanism_counts": [
            {"mechanism": m, "count": c} for m, c in top_mechanisms
        ],
        "fallback_reasons": (
            [{"cluster_id": cluster_id, "reason": parse_error}]
            if parse_error is not None
            else []
        ),
        "transport_failures": [],
        "skill_hash": sh,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "screenshot_media_type": media_type,
        "screenshot_bytes": screenshot_bytes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke for L4 audit-decision-psychology on one enriched cluster. "
            "Runs either text-only or multimodal (text + PNG) and writes "
            "verdicts / native / provenance with a per-(model, modality) "
            "suffix so a matched-model eval can run from a single bash loop."
        )
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--screenshot", type=Path, default=DEFAULT_SCREENSHOT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--modality",
        choices=("text", "image"),
        default="image",
        help=(
            "'image' = user turn carries PNG + cluster XML (multimodal); "
            "'text' = user turn is cluster XML only. Default: image."
        ),
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--suffix",
        default=None,
        help=(
            "Override the auto-generated filename suffix. Default derives "
            "from (model, modality): '_opus47' for text, "
            "'_opus47_multimodal' for image. Used only when you need to "
            "force a specific file layout (e.g. custom eval sweep)."
        ),
    )
    args = parser.parse_args(argv)

    include_image = args.modality == "image"

    cluster = _load_cluster(args.input)
    if include_image:
        media_type, image_b64 = _load_screenshot(args.screenshot)
        screenshot_bytes: int | None = args.screenshot.stat().st_size
    else:
        media_type = None
        image_b64 = None
        screenshot_bytes = None
    sh = skill_hash()

    if args.suffix is None:
        short = _short_model_name(args.model)
        suffix = f"_{short}_multimodal" if include_image else f"_{short}"
    else:
        suffix = args.suffix

    if include_image:
        ss_info = f"screenshot={args.screenshot.name} ({screenshot_bytes} bytes, {media_type}) "
    else:
        ss_info = "screenshot=— "
    print(
        f"smoke: cluster={cluster.cluster_id} model={args.model} "
        f"modality={args.modality} {ss_info}"
        f"skill_hash={sh[:16]}…",
        flush=True,
    )

    message, text = _run(
        cluster=cluster,
        media_type=media_type,
        image_b64=image_b64,
        model=args.model,
        include_image=include_image,
    )

    usage = message.usage
    input_tokens = int(usage.input_tokens)
    output_tokens = int(usage.output_tokens)

    verdict_id = _verdict_id(SKILL_ID, cluster.cluster_id)
    produced_at = datetime.now(UTC)
    cluster_stem = cluster.cluster_id.replace("_", "")
    native_stem = f"l4_verdicts_audit_decision_psychology_{cluster_stem}{suffix}"
    verdicts_path = args.out_dir / f"{native_stem}.jsonl"
    native_path = args.out_dir / f"{native_stem}.native.jsonl"
    provenance_path = args.out_dir / f"{native_stem}.provenance.json"

    native_ref = f"{native_path.name}#{verdict_id}"

    parse_error: str | None = None
    payload: dict | None = None
    try:
        payload = parse_audit_response(
            text, n_quotes=len(cluster.representative_quotes)
        )
    except AuditParseError as e:
        parse_error = str(e)

    if payload is not None:
        violations = _build_heuristic_violations(payload, cluster)
        status = "audited"
        native_row_payload = payload
    else:
        violations = []
        status = "fallback"
        native_row_payload = _fallback_native(text, parse_error or "unknown")

    verdict = AuditVerdict(
        verdict_id=verdict_id,
        cluster_id=cluster.cluster_id,
        skill_id=SKILL_ID,
        relevant_heuristics=violations,
        native_payload_ref=native_ref,
        produced_at=produced_at,
        claude_model=args.model,
        skill_hash=sh,
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    verdicts_path.write_text(
        json.dumps(verdict.model_dump(mode="json"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    native_row = {
        "verdict_id": verdict_id,
        "cluster_id": cluster.cluster_id,
        "status": status,
        "payload": native_row_payload,
    }
    native_path.write_text(
        json.dumps(native_row, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    provenance_path.write_text(
        json.dumps(
            _build_provenance(
                cluster_id=cluster.cluster_id,
                model=args.model,
                modality=args.modality,
                payload=payload,
                parse_error=parse_error,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                sh=sh,
                media_type=media_type,
                screenshot_bytes=screenshot_bytes,
            ),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"done: status={status} findings={len(violations)} "
        f"input_tokens={input_tokens} output_tokens={output_tokens}\n"
        f"  verdicts:   {verdicts_path.relative_to(_REPO_ROOT)}\n"
        f"  native:     {native_path.relative_to(_REPO_ROOT)}\n"
        f"  provenance: {provenance_path.relative_to(_REPO_ROOT)}",
        flush=True,
    )
    return 0 if status == "audited" else 1


if __name__ == "__main__":
    sys.exit(main())
