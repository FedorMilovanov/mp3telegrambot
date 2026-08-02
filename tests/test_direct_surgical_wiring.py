from __future__ import annotations

from pathlib import Path


def test_direct_entrypoint_installs_layers_in_safe_order() -> None:
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "tools/voxcpm2/direct_max_quality_cli.py").read_text(encoding="utf-8")
    guard = text.index("install_guard_contract()")
    universal = text.index("install_direct_runtime(globals())")
    surgical = text.index("install_surgical_runtime(globals())")
    recovery = text.index("install_main_failure_recovery(globals())")
    assert guard < universal < surgical < recovery


def test_preflight_guard_is_installed_before_generic_runtime_wrapper() -> None:
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "tools/voxcpm2/generic_clean_direct_runtime.py").read_text(
        encoding="utf-8"
    )
    assert text.index("install_guard_contract()") < text.index(
        "install_generic_preflight(globals())"
    )


def test_runtime_fingerprint_lists_surgical_and_backend_contracts() -> None:
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "tools/voxcpm2/clean_runtime_contract.py").read_text(
        encoding="utf-8"
    )
    for value in (
        "direct_surgical_guard.py",
        "direct_surgical_io.py",
        "direct_surgical_runtime.py",
        "audited_voxcpm2.py",
        "execution_plan.py",
        "model_profiles.py",
        "registry.py",
    ):
        assert value in text
