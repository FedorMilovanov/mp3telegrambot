#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Narrow release-health upgrade for the v6.6 direct quality release.

All established Dub environment, worker, renderer, cadence, pronunciation and
post-AAC checks remain authoritative. This module replaces only superseded
source-bed health and adds typical-reference plus transactional import checks.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from services.dub_worker_release import (
    LEGACY_IMPORT_POLICY,
    MASTER_MIX_POLICY,
    REFERENCE_POLICY,
    REFERENCE_SELECTION_POLICY,
    SOURCE_BED_POLICY,
    WORKER_RUNTIME,
)

POLICY = "truthful-master-reference-and-import-health-v3"
_SUPERSEDED_CHECK = "dialogue-suppressed-master"
_HOOKED = False


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _all(text: str, *markers: str) -> bool:
    return bool(text) and all(marker in text for marker in markers)


def _v66_quality_contract(repo: Path) -> tuple[bool, str]:
    root = Path(repo)
    voxcpm = root / "tools" / "voxcpm2"
    contract = _read(voxcpm / "spatial_bed_contract.py")
    master = _read(voxcpm / "master_monolithic_mix.py")
    qa = _read(voxcpm / "final_media_spatial_bed.py")
    reference = _read(voxcpm / "continuous_reference_policy" / "__init__.py")
    quality_facade = _read(voxcpm / "dub_quality_v4" / "__init__.py")
    tests = _read(root / "tests" / "test_spatial_bed_final_media_qa.py")
    master_tests = _read(root / "tests" / "test_dialogue_suppressed_master.py")
    reference_tests = _read(root / "tests" / "test_continuous_reference_typical_f0.py")
    import_tests = _read(root / "tests" / "test_legacy_facade_module_registration.py")

    checks = {
        "zero-source-contract": _all(
            contract,
            f'POLICY = "{MASTER_MIX_POLICY}"',
            'QA_POLICY = "post-aac-zero-source-bed-v2"',
            f'SOURCE_BED_POLICY = "{SOURCE_BED_POLICY}"',
            "CENTER_FLOOR_RATIO = 0.0",
            "MAX_CENTER_FLOOR = 0.0",
            "SIDE_BED_RATIO = 0.0",
            '"source_bed_applied": False',
            '"applied_original_level": 0.0',
            '"original_mid_and_side_may_both_contain_dialogue"',
        ),
        "russian-only-filter": _all(
            master,
            "from tools.voxcpm2.spatial_bed_contract import (",
            "SOURCE_BED_POLICY",
            "def build_dialogue_suppressed_mix(",
            'if float(levels["applied_original_level"]) != 0.0:',
            'f"[1:a]asetpts=PTS-STARTPTS,highpass=f=35,volume={russian_gain:.9f},"',
            "source is audit input, never a mix stem",
            "source_bed_applied=False",
            "_legacy.build_constant_mix = build_dialogue_suppressed_mix",
            "Importing the module is enough to make direct callers safe",
        ),
        "post-aac-zero-source": _all(
            qa,
            "POLICY = spatial_bed_contract.QA_POLICY",
            "def estimate_spatial_bed(",
            "estimated_center_level",
            "estimated_side_level",
            "normalized_residual",
        ),
        "master-negative-regressions": _all(
            tests,
            "test_old_full_eighteen_percent_source_bed_is_rejected",
            "test_old_side_only_bed_is_rejected_when_side_contains_speech",
            "test_post_aac_accepts_russian_only_mix_for_nonzero_requested_setting",
        ) and _all(
            master_tests,
            "test_import_immediately_overrides_legacy_mixer_and_calibration",
            "test_requested_source_level_is_audit_only_and_applied_level_is_zero",
            "test_mix_graph_uses_only_russian_audio",
            'assert "[0:a]" not in graph',
        ),
        "typical-continuous-reference": _all(
            reference,
            f'POLICY = "{REFERENCE_POLICY}"',
            f'SELECTION_POLICY = "{REFERENCE_SELECTION_POLICY}"',
            'RETIRED_SELECTION_POLICY = "absolute-f0-low-bias-retired-v1"',
            "def _quality_score(",
            "def _candidate_windows(",
            "robust_median = float(np.median(median_values))",
            "robust_p90 = float(np.median(p90_values))",
            "median_distance * MEDIAN_F0_WEIGHT",
            "p90_distance * P90_F0_WEIGHT",
            "_legacy._candidate_windows = _candidate_windows",
        ) and _all(
            reference_tests,
            "test_equal_quality_windows_choose_typical_pitch_not_lowest_pitch",
            'assert selected["stats"]["f0_median"] == 170.0',
            "test_quality_metrics_remain_part_of_reference_ranking",
        ),
        "transactional-legacy-import": _all(
            quality_facade,
            "_previous_legacy = sys.modules.get(_SPEC.name)",
            "sys.modules[_SPEC.name] = _legacy",
            "_SPEC.loader.exec_module(_legacy)",
            "sys.modules.pop(_SPEC.name, None)",
            "sys.modules[_SPEC.name] = _previous_legacy",
        ) and _all(
            import_tests,
            "test_dub_quality_legacy_module_is_registered_for_dataclasses",
            "test_dub_quality_import_succeeds_in_fresh_python_process",
            "test_registration_is_transactional_in_source",
            "sys.modules.get(legacy.__name__) is legacy",
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        return False, "Dub v6.6 quality-contract не прошёл: " + ", ".join(failed)
    return True, (
        "Russian-only direct master: requested source level is audit-only; applied center/side "
        "are zero; speech-bearing original is absent from the FFmpeg graph; post-AAC center "
        "and side leakage regressions are fail-closed; continuous reference selection is "
        "quality-gated and ranked around the speaker's robust median F0; legacy dataclass "
        f"modules use {LEGACY_IMPORT_POLICY} before execution with rollback on failure"
    )


def _v65_quality_contract(repo: Path) -> tuple[bool, str]:
    """Compatibility alias retained for existing callers."""
    return _v66_quality_contract(repo)


def _russian_only_master_contract(repo: Path) -> tuple[bool, str]:
    """Compatibility alias retained for existing tests and diagnostics."""
    return _v66_quality_contract(repo)


def _upgrade_monolithic_contract(title: Any) -> None:
    current = title._monolithic_static_contract
    if getattr(current, "_dub_v66_quality_contract", False):
        return

    def v66_monolithic_contract(repo: Path) -> tuple[bool, str]:
        ok, detail = current(Path(repo))
        remaining: list[str] = []
        base_detail = str(detail)
        if not ok:
            prefix = "monolithic-контракты не прошли: "
            if not base_detail.startswith(prefix):
                return False, base_detail
            failed = [
                item.strip()
                for item in base_detail[len(prefix):].split(",")
                if item.strip()
            ]
            remaining = [item for item in failed if item != _SUPERSEDED_CHECK]
        current_ok, current_detail = _v66_quality_contract(Path(repo))
        if remaining:
            return False, "monolithic-контракты не прошли: " + ", ".join(remaining)
        if not current_ok:
            return False, current_detail
        stable_detail = base_detail if ok else (
            "semantic-breath, one-identity, pronunciation, fail-closed timeline, noise and "
            "fingerprint contracts active"
        )
        return True, stable_detail + "; " + current_detail

    v66_monolithic_contract._dub_v66_quality_contract = True  # type: ignore[attr-defined]
    title._monolithic_static_contract = v66_monolithic_contract
    legacy = getattr(title, "_legacy", None)
    if legacy is not None and hasattr(legacy, "_monolithic_static_contract"):
        legacy._monolithic_static_contract = v66_monolithic_contract


def _install_after_title_policy() -> None:
    from services import dub_title_policy as title

    _upgrade_monolithic_contract(title)


def install_release_health_hook() -> None:
    """Wrap the regular title installer so current release health is applied last."""
    global _HOOKED
    if _HOOKED:
        return
    from services import dub_title_policy as title

    current: Callable[..., Any] = title.install_dub_title_policy
    if getattr(current, "_dub_v66_release_health_hook", False):
        _HOOKED = True
        return

    def install_dub_title_policy_v66(*args: Any, **kwargs: Any) -> Any:
        result = current(*args, **kwargs)
        _install_after_title_policy()
        return result

    install_dub_title_policy_v66._dub_v66_release_health_hook = True  # type: ignore[attr-defined]
    title.install_dub_title_policy = install_dub_title_policy_v66
    _HOOKED = True


__all__ = [
    "POLICY",
    "WORKER_RUNTIME",
    "_russian_only_master_contract",
    "_v65_quality_contract",
    "_v66_quality_contract",
    "install_release_health_hook",
]
