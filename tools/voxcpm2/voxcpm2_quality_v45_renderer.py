#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the proven Quality v4 renderer over the professional v4.5 adapter."""
from __future__ import annotations

import os, runpy
from pathlib import Path


def main() -> None:
    legacy = Path(os.environ.get("VOXCPM_ORIGINAL_RENDERER", "")).resolve()
    if not legacy.is_file():
        raise RuntimeError(f"Исходный NoChew renderer не найден: {legacy}")
    adapter = Path(__file__).resolve().parent / "voxcpm2_professional_adapter_v45.py"
    renderer = Path(__file__).resolve().parent / "voxcpm2_quality_v4_renderer.py"
    os.environ["VOXCPM_LEGACY_RENDERER"] = str(legacy)
    os.environ["VOXCPM_ORIGINAL_RENDERER"] = str(adapter)
    namespace = runpy.run_path(str(renderer), run_name="voxcpm2_quality_v45")
    namespace["main"]()


if __name__ == "__main__":
    main()
