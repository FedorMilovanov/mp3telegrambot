from __future__ import annotations

from pathlib import Path

from tools.voxcpm2 import clean_production_core as core
from tools.voxcpm2 import clean_runtime_contract as contract


def test_clean_runtime_and_core_import_through_compatibility_packages() -> None:
    assert Path(contract.__file__).name == "__init__.py"
    assert Path(core.__file__).name == "__init__.py"


def test_runtime_facade_fingerprints_itself_and_strict_core() -> None:
    required = {
        "tools/voxcpm2/clean_runtime_contract/__init__.py",
        "tools/voxcpm2/clean_production_core/__init__.py",
    }
    assert required.issubset(set(contract._RENDER_MODULES))
    assert contract._legacy._RENDER_MODULES == contract._RENDER_MODULES


def test_core_facade_patches_all_legacy_preflight_calls() -> None:
    assert core._legacy._finite is core._finite
    assert core._legacy._mark_and_validate_segments is core._mark_and_validate_segments
    assert callable(core.render_and_master)
    assert callable(core.build_render_segments)
    assert callable(core.build_direct_segments)
