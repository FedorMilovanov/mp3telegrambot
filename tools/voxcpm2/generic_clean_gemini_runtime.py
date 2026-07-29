#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gemini MAX entrypoint for the clean direct Dub production path."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from services.dub_title_policy import install_voxcpm_title_policy
from tools.voxcpm2 import clean_production_core as clean
from tools.voxcpm2 import clean_source_download
from tools.voxcpm2 import continuous_reference_policy
from tools.voxcpm2 import controlled_reference_gate
from tools.voxcpm2 import expressive_continuity
from tools.voxcpm2 import expressive_translation
from tools.voxcpm2 import generic_gemini_runtime as checked
from tools.voxcpm2 import generic_project_runtime as production

_BASE_ACQUIRE_TRANSCRIPT = production.acquire_transcript


def _install_clean_runtime_adapters() -> None:
    """Keep hardened download/Gemini routing, but never install a TTS guard."""
    hardened = production.hardened
    hardened.download_source = clean_source_download.download_source
    hardened.pipeline.download_source = clean_source_download.download_source
    hardened.pipeline.download_captions = hardened.download_captions
    hardened.pipeline.gemini_json = hardened.gemini_json
    install_voxcpm_title_policy(hardened)
    hardened._install_project_title_standard()
    production.log(
        "clean adapters: verified yt-dlp source + Gemini pool; "
        "canonical title policy; TTS guard disabled"
    )


def _acquire_transcript_with_actual_language(
    *args: Any,
    **kwargs: Any,
) -> tuple[list[Any], str, str]:
    """Attach the selected caption/Whisper language to translation metadata."""
    result = _BASE_ACQUIRE_TRANSCRIPT(*args, **kwargs)
    cues, caption_origin, source_language = result
    metadata = kwargs.get("metadata")
    if metadata is None and len(args) >= 4:
        metadata = args[3]
    if isinstance(metadata, dict):
        language = str(source_language or "unknown")
        metadata["language"] = language
        metadata["source_language"] = language
    return cues, caption_origin, source_language


def _run_clean_voxcpm_and_master(
    *,
    root: Path,
    request: dict[str, Any],
    source: Path,
    cues: list[Any],
    duration: float,
    segments_json: Path,
    final_mixed: Path,
    final_russian: Path,
) -> Path:
    extended, composite = continuous_reference_policy.build_calm_references(
        source=source,
        cues=cues,
        duration=duration,
        reference_dir=root / "references",
    )
    planned = expressive_continuity.plan_json(
        source=source,
        segments_path=segments_json,
        duration=duration,
        report_path=root / "output" / "expressive_continuity.json",
    )
    _expressive_built, reference_detail = controlled_reference_gate.build_or_keep_calm(
        source=source,
        segments=planned,
        output=composite,
        identity_reference=extended,
    )
    production.log(
        "source-guided emotional arc prepared; Russian text preserved; "
        + reference_detail
    )
    return clean.render_and_master(
        root=root,
        request=request,
        source=source,
        duration=duration,
        segments_json=segments_json,
        extended_reference=extended,
        composite_reference=composite,
        final_mixed=final_mixed,
        final_russian=final_russian,
        force_fresh=True,
    )


def main() -> None:
    production.hardened.install_runtime_adapters = _install_clean_runtime_adapters
    production.acquire_transcript = _acquire_transcript_with_actual_language
    production.pipeline.group_cues = clean.group_source_cues
    production.translate_groups_max = expressive_translation.translate_groups
    production.parse_manual_vtt = checked.parse_creator_vtt_preserving_text
    production._build_render_segments = clean.build_render_segments
    production._run_voxcpm_and_master = _run_clean_voxcpm_and_master
    production.main()
    root = production.project_root(production.current_project_id())
    checked.validate_completed_outputs(root)
    production.log("=== CLEAN GEMINI DIRECT PRODUCTION CONTRACT: OK ===")


if __name__ == "__main__":
    main()
