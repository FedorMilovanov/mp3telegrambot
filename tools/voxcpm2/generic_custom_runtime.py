#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Source-owned custom-translation Dub Studio entrypoint."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.voxcpm2 import clean_production_core as clean
from tools.voxcpm2 import clean_request_settings
from tools.voxcpm2 import clean_source_download
from tools.voxcpm2 import continuous_reference_policy
from tools.voxcpm2 import controlled_reference_gate
from tools.voxcpm2 import expressive_continuity
from tools.voxcpm2 import generic_project_runtime as production
from tools.voxcpm2 import strict_translation_payload

POLICY = "source-owned-clean-custom-v1"


def _clean_source_groups(cues: list[Any]) -> list[dict[str, Any]]:
    groups = clean.group_source_cues(cues)
    for group in groups:
        group["source"] = group.pop("english")
    return groups


def _run_clean_speech_and_master(
    *, root: Path, request: dict[str, Any], source: Path, cues: list[Any],
    duration: float, segments_json: Path, final_mixed: Path, final_russian: Path,
) -> Path:
    extended, composite = continuous_reference_policy.build_calm_references(
        source=source, cues=cues, duration=duration, reference_dir=root / "references"
    )
    planned = expressive_continuity.plan_json(
        source=source,
        segments_path=segments_json,
        duration=duration,
        report_path=root / "output" / "expressive_continuity.json",
    )
    _built, detail = controlled_reference_gate.build_or_keep_calm(
        source=source, segments=planned, output=composite, identity_reference=extended
    )
    production.log("source-guided emotional arc prepared; custom text preserved; " + detail)
    return clean.render_and_master(
        root=root, request=request, source=source, duration=duration,
        segments_json=segments_json, extended_reference=extended,
        composite_reference=composite, final_mixed=final_mixed,
        final_russian=final_russian, force_fresh=True,
    )


def _finalize(root: Path, request: dict[str, Any]) -> None:
    clean_request_settings.repair_manifest(root, request)


def main() -> None:
    route = production.ProjectRoute(
        download_source=clean_source_download.download_source,
        acquire_transcript=production.acquire_transcript,
        group_source=_clean_source_groups,
        translate_groups=production.translate_groups_max,
        validate_translation=strict_translation_payload.validate_full,
        build_render_segments=clean.build_render_segments,
        run_speech_and_master=_run_clean_speech_and_master,
        delay_ms=clean_request_settings.russian_delay_ms,
        finalize=_finalize,
    )
    production.main(route)


if __name__ == "__main__":
    main()
