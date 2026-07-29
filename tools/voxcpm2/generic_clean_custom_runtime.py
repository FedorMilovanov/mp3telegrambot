#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Custom-translation entrypoint for the clean direct Dub production path."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from services.dub_title_policy import install_voxcpm_title_policy
from tools.voxcpm2 import clean_production_core as clean
from tools.voxcpm2 import clean_source_download
from tools.voxcpm2 import continuous_reference_policy
from tools.voxcpm2 import controlled_reference_gate
from tools.voxcpm2 import expressive_continuity
from tools.voxcpm2 import generic_project_runtime as production
from tools.voxcpm2 import strict_translation_payload


def _install_clean_runtime_adapters() -> None:
    hardened = production.hardened
    hardened.download_source = clean_source_download.download_source
    hardened.pipeline.download_source = clean_source_download.download_source
    hardened.pipeline.download_captions = hardened.download_captions
    hardened.pipeline.gemini_json = hardened.gemini_json
    install_voxcpm_title_policy(hardened)
    hardened._install_project_title_standard()
    production.log(
        "clean adapters: verified yt-dlp source + canonical title route; "
        "TTS guard disabled"
    )


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
        "source-guided emotional arc prepared; custom text preserved; "
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
    production.pipeline.group_cues = clean.group_source_cues
    production._validate_translation_payload = strict_translation_payload.validate_full
    production._build_render_segments = clean.build_render_segments
    production._run_voxcpm_and_master = _run_clean_voxcpm_and_master
    production.main()


if __name__ == "__main__":
    main()
