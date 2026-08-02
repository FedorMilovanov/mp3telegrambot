#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dub worker with universal candidate-aware progress hardening."""
from pathlib import Path

_ORIGINAL_NAME = __name__
_BASE = Path(__file__).with_name("_dub_worker_hardened_base.py")
if not _BASE.is_file():
    raise RuntimeError(f"Missing Dub worker base snapshot: {_BASE}")
globals()["__name__"] = "tools.voxcpm2._dub_worker_hardened_base_exec"
exec(compile(_BASE.read_text(encoding="utf-8-sig"), str(_BASE), "exec"), globals())
globals()["__name__"] = _ORIGINAL_NAME

from tools.voxcpm2.direct_universal_runtime import install_worker_progress

install_worker_progress(globals())

if _ORIGINAL_NAME == "__main__":
    main()
