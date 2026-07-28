#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ready-SRT entrypoint for the clean direct Dub production path."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.voxcpm2 import clean_production_core as clean
from tools.voxcpm2 import generic_direct_runtime as production


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
    production.group_srt_cues = clean.group_ready_srt
    production._build_direct_segments = clean.build_direct_segments
    production._run_voxcpm_and_master = _run_clean_voxcpm_and_master
    production.main()


if __name__ == "__main__":
    main()
