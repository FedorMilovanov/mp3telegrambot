#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stable entrypoint for the hardened VoxCPM2 semantic wrapper.

The semantic guard launches this path directly. Keep the implementation beside
its production renderer example, but expose a stable top-level executable so
Windows workers do not fail before synthesis starts.
"""
from __future__ import annotations

import runpy
from pathlib import Path


def main() -> None:
    implementation = (
        Path(__file__).resolve().parent
        / "examples"
        / "john_piper_z20py4yqhyq"
        / "voxcpm2_cpu_semantic_wrapper.py"
    )
    if not implementation.is_file():
        raise RuntimeError(f"Hardened VoxCPM2 wrapper implementation not found: {implementation}")
    runpy.run_path(str(implementation), run_name="__main__")


if __name__ == "__main__":
    main()
