#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Universal direct renderer entrypoint with layered production hardening."""
from pathlib import Path

_ORIGINAL_NAME = __name__
_BASE = Path(__file__).with_name("_direct_max_quality_cli_base.py")
if not _BASE.is_file():
    raise RuntimeError(f"Missing direct renderer base snapshot: {_BASE}")
globals()["__name__"] = "tools.voxcpm2._direct_max_quality_cli_base_exec"
exec(compile(_BASE.read_text(encoding="utf-8-sig"), str(_BASE), "exec"), globals())
globals()["__name__"] = _ORIGINAL_NAME

from tools.voxcpm2.direct_surgical_guard import install_guard_contract
from tools.voxcpm2.direct_universal_runtime import install_direct_runtime
from tools.voxcpm2.direct_surgical_runtime import install_surgical_runtime
from tools.voxcpm2.direct_surgical_polish_v2 import install_global_polish
from tools.voxcpm2.direct_final_audit_v3 import install_final_audit
from tools.voxcpm2.direct_failure_recovery import install_main_failure_recovery

install_guard_contract()
install_direct_runtime(globals())
install_surgical_runtime(globals())
install_global_polish()
install_final_audit(globals())
install_main_failure_recovery(globals())

if _ORIGINAL_NAME == "__main__":
    _main = globals().get("main")
    if not callable(_main):
        raise RuntimeError("Direct renderer base did not export main().")
    _main()
