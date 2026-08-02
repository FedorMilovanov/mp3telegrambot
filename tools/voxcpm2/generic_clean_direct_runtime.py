#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Universal ready-SRT runtime with pre-reference timing validation."""
from pathlib import Path

_ORIGINAL_NAME = __name__
_BASE = Path(__file__).with_name("_generic_clean_direct_runtime_base.py")
if not _BASE.is_file():
    raise RuntimeError(f"Missing clean direct runtime base snapshot: {_BASE}")
globals()["__name__"] = "tools.voxcpm2._generic_clean_direct_runtime_base_exec"
exec(compile(_BASE.read_text(encoding="utf-8-sig"), str(_BASE), "exec"), globals())
globals()["__name__"] = _ORIGINAL_NAME

from tools.voxcpm2.direct_surgical_guard import install_guard_contract
from tools.voxcpm2.direct_surgical_polish_v2 import install_global_polish
from tools.voxcpm2.direct_universal_runtime import install_generic_preflight

install_guard_contract()
install_global_polish()
install_generic_preflight(globals())

if _ORIGINAL_NAME == "__main__":
    main()
