#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Universal ready-SRT runtime with explicit composed production contracts."""
from pathlib import Path
from typing import Any

_ORIGINAL_NAME = __name__
_BASE = Path(__file__).with_name("_generic_clean_direct_runtime_base.py")
if not _BASE.is_file():
    raise RuntimeError(f"Missing clean direct runtime base snapshot: {_BASE}")
globals()["__name__"] = "tools.voxcpm2._generic_clean_direct_runtime_base_exec"
exec(compile(_BASE.read_text(encoding="utf-8-sig"), str(_BASE), "exec"), globals())
globals()["__name__"] = _ORIGINAL_NAME

from tools.voxcpm2 import clean_source_download
from tools.voxcpm2 import continuous_reference_policy
from tools.voxcpm2 import controlled_reference_gate
from tools.voxcpm2 import expressive_continuity
from tools.voxcpm2.direct_surgical_guard import install_guard_contract
from tools.voxcpm2.direct_surgical_polish_v2 import install_global_polish
from tools.voxcpm2.direct_universal_runtime import install_generic_preflight


production = globals().get("production")
if production is None or getattr(production, "hardened", None) is None:
    raise RuntimeError("Clean direct base did not export production.hardened.")
hardened = production.hardened
hardened.download_source = clean_source_download.download_source
hardened.pipeline.download_source = clean_source_download.download_source



def _continuous_reference_contract(**kwargs: Any) -> Any:
    return continuous_reference_policy.build_calm_references(**kwargs)


def _identity_reference_contract(
    *,
    source: Path,
    segments: list[dict[str, Any]],
    output: Path,
    extended: Path,
) -> Any:
    return controlled_reference_gate.build_or_keep_calm(
        source=source,
        segments=segments,
        output=output,
        identity_reference=extended,
    )


def _expression_contract(**kwargs: Any) -> Any:
    return expressive_continuity.plan_json(**kwargs)


def _resume_contract(*, force_fresh=False) -> bool:
    """Keeping this False makes a late failed segment resumable."""
    if force_fresh:
        raise RuntimeError("Ready-SRT direct route must preserve compatible checkpoints.")
    return False


DIRECT_FORCE_FRESH = _resume_contract()
REFERENCE_BUILDERS = (
    _continuous_reference_contract,
    _identity_reference_contract,
    _expression_contract,
)

install_guard_contract()
install_global_polish()
install_generic_preflight(globals())

if _ORIGINAL_NAME == "__main__":
    _main = globals().get("main")
    if not callable(_main):
        raise RuntimeError("Clean direct runtime base did not export main().")
    _main()
