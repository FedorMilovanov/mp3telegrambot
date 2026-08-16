from __future__ import annotations

from pathlib import Path


RETIRED = (
    "services/cut_mode_source_policy.py",
    "services/project_runtime_hardening.py",
    "tools/voxcpm2/generic_direct_checked_runtime.py",
    "tools/voxcpm2/generic_clean_direct_runtime.py",
    "tools/voxcpm2/generic_clean_gemini_runtime.py",
    "tools/voxcpm2/generic_short_runtime.py",
    "tools/voxcpm2/preflight_json_protocol.py",
    "tools/voxcpm2/master_monolithic_mix.py",
)


def test_retired_runtime_surgery_modules_stay_deleted() -> None:
    root = Path(__file__).resolve().parents[1]
    assert not [path for path in RETIRED if (root / path).exists()]


def test_active_routes_do_not_reintroduce_common_surgery_primitives() -> None:
    root = Path(__file__).resolve().parents[1]
    active = (
        "tools/voxcpm2/generic_project_runtime.py",
        "tools/voxcpm2/generic_direct_runtime.py",
        "tools/voxcpm2/generic_gemini_runtime.py",
        "tools/voxcpm2/generic_custom_runtime.py",
        "tools/voxcpm2/master_direct_russian_only.py",
        "tools/voxcpm2/dub_job_preflight.py",
    )
    for relative in active:
        source = (root / relative).read_text(encoding="utf-8")
        assert "sys.modules" not in source, relative
        assert "def install_runtime" not in source, relative
        assert "setattr(module" not in source, relative
