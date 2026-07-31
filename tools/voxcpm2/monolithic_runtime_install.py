#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install monolithic renderer/master routing in the ready-SRT child process."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

POLICY = "monolithic-ready-srt-runtime-routing-v1"
MASTER_NAME = "master_monolithic_mix.py"
RENDERER_NAME = "voxcpm2_cpu_shorts_production.py"
_INSTALLED = False


def _basename(value: Any) -> str:
    return re.split(r"[\\/]", str(value or ""))[-1].casefold()


def _renderer_paths(repo: Path) -> tuple[Path, Path]:
    root = Path(repo).resolve()
    renderer = (
        root
        / "tools"
        / "voxcpm2"
        / "examples"
        / "john_piper_z20py4yqhyq"
        / RENDERER_NAME
    )
    master = root / "tools" / "voxcpm2" / MASTER_NAME
    if not renderer.is_file() or not master.is_file():
        raise RuntimeError(
            "Monolithic production renderer/master не найдены: "
            f"renderer={renderer}; master={master}"
        )
    return renderer, master


def _is_master_command(command: Any) -> bool:
    return bool(
        isinstance(command, (list, tuple))
        and len(command) >= 2
        and _basename(command[0]).startswith("python")
        and _basename(command[1]) in {MASTER_NAME.casefold(), "master_constant_mix.py"}
    )


def install() -> None:
    global _INSTALLED
    from tools.voxcpm2 import clean_production_core

    legacy = getattr(clean_production_core, "_legacy", None)
    if legacy is None:
        raise RuntimeError("Clean production core не предоставляет runtime facade.")
    legacy._renderer_paths = _renderer_paths
    clean_production_core._renderer_paths = _renderer_paths
    clean_production_core._is_master_command = _is_master_command
    _INSTALLED = True


__all__ = [
    "MASTER_NAME",
    "POLICY",
    "RENDERER_NAME",
    "_is_master_command",
    "_renderer_paths",
    "install",
]
