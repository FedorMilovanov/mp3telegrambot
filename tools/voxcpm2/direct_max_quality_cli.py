#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Universal direct renderer entrypoint with project-wide VoxCPM2 hardening."""
from pathlib import Path

_ORIGINAL_NAME = __name__
_BASE = Path(__file__).with_name("_direct_max_quality_cli_base.py")
if not _BASE.is_file():
    raise RuntimeError(f"Missing direct renderer base snapshot: {_BASE}")
globals()["__name__"] = "tools.voxcpm2._direct_max_quality_cli_base_exec"
exec(compile(_BASE.read_text(encoding="utf-8-sig"), str(_BASE), "exec"), globals())
globals()["__name__"] = _ORIGINAL_NAME

from tools.voxcpm2.direct_universal_runtime import install_direct_runtime

install_direct_runtime(globals())

if _ORIGINAL_NAME == "__main__":
    main()
