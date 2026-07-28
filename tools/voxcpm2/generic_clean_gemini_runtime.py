#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gemini MAX entrypoint for the clean direct Dub production path."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.voxcpm2 import clean_production_core as clean
from tools.voxcpm2 import generic_gemini_runtime as checked
from tools.voxcpm2 import generic_project_runtime as production


def _install_clean_runtime_adapters() -> None:
    """Keep hardened download/Gemini routing, but never install a TTS guard."""
    hardened = production.hardened
    hardened.pipeline.download_source = hardened.download_source
    hardened.pipeline.download_captions = hardened.download_captions
    hardened.pipeline.gemini_json = hardened.gemini_json
    hardened._install_project_title_standard()
    production.log("clean adapters: yt-dlp + Gemini pool; TTS guard disabled")


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
    extended, composite = clean.build_calm_references(
        source=source,
        cues=cues,
        duration=duration,
        reference_dir=root / "references",
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
    )


def main() -> None:
    # Preparation may be configured, but the renderer itself is never wrapped.
    production.hardened.install_runtime_adapters = _install_clean_runtime_adapters
    production.pipeline.group_cues = clean.group_source_cues
    production.parse_manual_vtt = checked.parse_creator_vtt_preserving_text
    production._build_render_segments = clean.build_render_segments
    production._run_voxcpm_and_master = _run_clean_voxcpm_and_master
    production.main()
    root = production.project_root(production.current_project_id())
    checked.validate_completed_outputs(root)
    production.log("=== CLEAN GEMINI DIRECT PRODUCTION CONTRACT: OK ===")


if __name__ == "__main__":
    main()
