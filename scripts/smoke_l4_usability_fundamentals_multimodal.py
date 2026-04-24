"""One-shot smoke for the L4 usability-fundamentals audit — text or multimodal.

Companion to the Norman "thin spine" module
:mod:`auditable_design.layers.l4_audit`. Takes one enriched cluster,
calls the Anthropic SDK directly with either a plain text user turn or
a text+image user turn, parses the response through the same parser
the module uses, and writes verdicts / native / provenance next to
the module's own outputs. Zero cache interaction.

The script covers both modalities so a matched-model eval (opus46 /
sonnet46 / opus47 × text / multimodal) is one bash loop away without
two divergent smoke entry points. Structurally identical to
``smoke_l4_interaction_design_multimodal.py`` — only the module
import and provenance shape (Norman has no per-finding structured
fields beyond the standard ``dimension`` / ``evidence_source`` axes,
so the provenance is the leanest of the L4 smokes) differ.

Output
------
Mirrors the module's native output contract, with a per-(cluster,
model, modality) suffix so all N runs in a matched eval coexist:

* text:       ``…_{clusterNN}_<modelshort>.{jsonl,native.jsonl,provenance.json}``
* multimodal: ``…_{clusterNN}_<modelshort>_multimodal.{jsonl,…}``

Cluster stem is derived from the loaded cluster's ``cluster_id`` (e.g.
``cluster_02`` → ``cluster02``). Model-short mapping: claude-opus-4-6
→ opus46, claude-sonnet-4-6 → sonnet46, claude-opus-4-7 → opus47,
claude-haiku-4-5 → haiku45.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import anthropic

# Make `src/` importable without requiring `uv run` wrapping.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from auditable_design.claude_client import _omits_sampling_params  # noqa: E402
from auditable_design.layers.l4_audit import (  # noqa: E402
    DIMENSION_KEYS,
    MAX_TOKENS,
    SKILL_ID,
    SYSTEM_PROMPT,
    TEMPERATURE,
    AuditParseError,
    _build_heuristic_violations,
    _fallback_native,
    _verdict_id,
    build_user_message,
    parse_audit_response,
    skill_hash,
)
from auditable_design.schemas import AuditVerdict, InsightCluster  # noqa: E402

DEFAULT_INPUT = (
    _REPO_ROOT
    / "data/derived/l4_audit/audit_usability_fundamentals/audit_usability_fundamentals_input.jsonl"
)
DEFAULT_SCREENSHOT = _REPO_ROOT / "data/artifacts/ui/duolingo_streak_modal.png"
DEFAULT_OUT_DIR = _REPO_ROOT / "data/derived/l4_audit/audit_usability_fundamentals"
DEFAULT_MODEL = "claude-sonnet-4-6"


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
    """Project the full model id to the compact label used in filenames."""
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
    content (image first, then the ``<cluster>`` XML text).
    """
    client = anthropic.Anthropic()
    user_text = build_user_message(cluster)

    if include_image:
        if media_type is None or image_b64 is None:
            raise ValueError("include_image=True requires media_type and image_b64")
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
        user_content = user_text

    kwargs: dict[str, object] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
    }
    if not _omits_sampling_params(model):
        kwargs["temperature"] = TEMPERATURE
    message = client.messages.create(**kwargs)
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
    """Mirror of ``l4_audit.build_provenance`` for one-cluster smoke.

    Shape parallels the full-module provenance. Norman has no per-
    finding structured fields (posture, product_type, etc.) so the
    provenance is the leanest of the L4 smokes: dim-score totals, a
    Nielsen severity histogram, findings_count, and the token / media
    metadata every L4 smoke records.
    """
    audited = 1 if payload is not None else 0
    fallback = 1 if payload is None else 0

    dim_totals = {k: 0 for k in DIMENSION_KEYS}
    findings_count = 0
    severity_hist: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}

    if payload is not None:
        for k in DIMENSION_KEYS:
            dim_totals[k] += int(payload["dimension_scores"][k])
        for f in payload["findings"]:
            findings_count += 1
            sev = int(f["severity"])
            severity_hist[sev] = severity_hist.get(sev, 0) + 1

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
            "Smoke for L4 audit-usability-fundamentals (Norman) on one "
            "enriched cluster. Runs either text-only or multimodal "
            "(text + PNG) and writes verdicts / native / provenance "
            "with a per-(cluster, model, modality) suffix so a matched-"
            "model eval can run from a single bash loop."
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
            "'_opus47_multimodal' for image."
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
    # Cluster stem derived from the loaded cluster's cluster_id — lets
    # one smoke handle any cluster without hardcoded filename prefixes.
    cluster_stem = cluster.cluster_id.replace("_", "")
    native_stem = f"l4_verdicts_audit_usability_fundamentals_{cluster_stem}{suffix}"
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
