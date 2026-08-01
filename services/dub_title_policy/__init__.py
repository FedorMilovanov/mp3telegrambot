#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Active Dub title policy and monolithic release health facade.

The sibling module owns Russian title casing and installation. This package
keeps that implementation and replaces historical source-text release checks
with checks against the modules Python actually imports.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from services.dub_worker_release import (
    BACKEND_COMMAND_POLICY,
    BACKEND_ENVIRONMENT_POLICY,
    EXPRESSION_POLICY,
    FAIL_CLOSED_IDENTITY_POLICY,
    LEGACY_IMPORT_POLICY,
    MASTER_MIX_POLICY,
    MONOLITHIC_TIMELINE_POLICY,
    MONOLITHIC_VOICE_POLICY,
    PRONUNCIATION_POLICY,
    PRONUNCIATION_VARIANT_POLICY,
    READY_SRT_GROUPING_POLICY,
    REFERENCE_POLICY,
    REFERENCE_SELECTION_POLICY,
    RUNTIME_ROUTING_POLICY,
    SEMANTIC_BLOCK_POLICY,
    SOURCE_BED_POLICY,
    SOURCE_PROSODY_ROLE_POLICY,
    SOURCE_RELATIVE_CONTINUITY_POLICY,
    TAIL_BRACKETING_POLICY,
    WORKER_RUNTIME,
)

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "dub_title_policy.py"
_SPEC = importlib.util.spec_from_file_location(
    "services._dub_title_policy_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить Dub title policy: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_previous_legacy = sys.modules.get(_SPEC.name)
sys.modules[_SPEC.name] = _legacy
try:
    _SPEC.loader.exec_module(_legacy)
except BaseException:
    if _previous_legacy is None:
        sys.modules.pop(_SPEC.name, None)
    else:
        sys.modules[_SPEC.name] = _previous_legacy
    raise

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_legacy_patch_health = _legacy._patch_health
RELEASE_CONTRACT_POLICY = "active-monolithic-dub-release-v1"


def _package_facade(module: Any) -> bool:
    value = getattr(module, "__file__", None)
    return bool(value and Path(value).name == "__init__.py")


def _monolithic_static_contract(repo: Path) -> tuple[bool, str]:
    from services.speech_backends import (
        BACKEND_COMMAND_POLICY as ACTIVE_BACKEND_COMMAND_POLICY,
        BACKEND_ENVIRONMENT_POLICY as ACTIVE_BACKEND_ENVIRONMENT_POLICY,
        default_backend,
    )
    from tools.voxcpm2 import direct_max_quality_cli
    from tools.voxcpm2 import direct_max_quality_render
    from tools.voxcpm2 import direct_monolith_contract
    from tools.voxcpm2 import direct_source_relative_continuity
    from tools.voxcpm2 import direct_tail_artifact
    from tools.voxcpm2 import direct_timeline_delivery_qa
    from tools.voxcpm2 import dub_quality_v4
    from tools.voxcpm2 import expressive_continuity
    from tools.voxcpm2 import master_monolithic_mix
    from tools.voxcpm2 import monolithic_runtime_install
    from tools.voxcpm2 import russian_pronunciation
    from tools.voxcpm2 import source_prosody_policy

    repo = Path(repo).resolve()
    facades = (
        dub_quality_v4,
        expressive_continuity,
        direct_monolith_contract,
        direct_max_quality_cli,
        direct_max_quality_render,
        direct_tail_artifact,
        direct_timeline_delivery_qa,
    )
    fingerprint = (
        repo / "tools" / "voxcpm2" / "clean_runtime_contract" / "__init__.py"
    )
    fingerprint_text = (
        fingerprint.read_text(encoding="utf-8") if fingerprint.is_file() else ""
    )
    required_fingerprints = (
        "tools/voxcpm2/dub_quality_v4/__init__.py",
        "tools/voxcpm2/expressive_continuity/__init__.py",
        "tools/voxcpm2/direct_monolith_contract/__init__.py",
        "tools/voxcpm2/direct_max_quality_cli/__init__.py",
        "tools/voxcpm2/direct_max_quality_render/__init__.py",
        "tools/voxcpm2/direct_tail_artifact/__init__.py",
        "tools/voxcpm2/direct_timeline_delivery_qa/__init__.py",
        "tools/voxcpm2/master_monolithic_mix.py",
    )
    backend = default_backend()
    capabilities = backend.capabilities()

    checks = {
        "facades": all(_package_facade(module) for module in facades),
        "semantic-breath": dub_quality_v4.POLICY == READY_SRT_GROUPING_POLICY,
        "pronunciation": (
            russian_pronunciation.POLICY == PRONUNCIATION_POLICY
            and russian_pronunciation.VARIANT_POLICY == PRONUNCIATION_VARIANT_POLICY
        ),
        "expression": expressive_continuity.POLICY == EXPRESSION_POLICY,
        "diagnostic-source": (
            source_prosody_policy.POLICY == SOURCE_PROSODY_ROLE_POLICY
            and direct_source_relative_continuity.POLICY
            == SOURCE_RELATIVE_CONTINUITY_POLICY
            and direct_source_relative_continuity.ABSOLUTE_GATE_OVERRIDE_ALLOWED is False
            and direct_source_relative_continuity.RANKING_PENALTY_ENABLED is False
        ),
        "candidate-identity": (
            direct_monolith_contract.POLICY == MONOLITHIC_VOICE_POLICY
            and callable(direct_monolith_contract.evaluate_candidate)
            and callable(direct_monolith_contract.candidate_hard_ok)
        ),
        "timeline": (
            direct_timeline_delivery_qa.POLICY == MONOLITHIC_TIMELINE_POLICY
            and callable(direct_timeline_delivery_qa.verify_timeline_delivery)
            and direct_max_quality_render.TIMELINE_COMPACTION_POLICY
            == "no-late-shift-monolithic-assembly-v2"
        ),
        "tail": (
            direct_tail_artifact.BRACKETING_POLICY == TAIL_BRACKETING_POLICY
            and callable(direct_tail_artifact.detect_late_broadband_tail)
        ),
        "master": (
            master_monolithic_mix.POLICY == MASTER_MIX_POLICY
            and monolithic_runtime_install.POLICY == RUNTIME_ROUTING_POLICY
            and monolithic_runtime_install.FAIL_CLOSED_IDENTITY_POLICY
            == FAIL_CLOSED_IDENTITY_POLICY
        ),
        "backend": (
            str(getattr(backend, "adapter_policy", "")).startswith(
                "voxcpm2-speech-backend-adapter-v"
            )
            and ACTIVE_BACKEND_COMMAND_POLICY == BACKEND_COMMAND_POLICY
            and ACTIVE_BACKEND_ENVIRONMENT_POLICY == BACKEND_ENVIRONMENT_POLICY
            and bool(getattr(capabilities, "continuation_context", False))
        ),
        "fingerprints": all(
            marker in fingerprint_text for marker in required_fingerprints
        ),
    }
    failed = [name for name, ok in checks.items() if not ok]
    detail = (
        f"{RELEASE_CONTRACT_POLICY}; semantic-breath={READY_SRT_GROUPING_POLICY}; "
        f"reference={REFERENCE_POLICY}/{REFERENCE_SELECTION_POLICY}; "
        f"semantic={SEMANTIC_BLOCK_POLICY}; source={SOURCE_PROSODY_ROLE_POLICY}; "
        f"legacy-import={LEGACY_IMPORT_POLICY}; tail={TAIL_BRACKETING_POLICY}; "
        f"master={MASTER_MIX_POLICY}; source-bed={SOURCE_BED_POLICY}; "
        f"backend={BACKEND_COMMAND_POLICY}; environment={BACKEND_ENVIRONMENT_POLICY}"
    )
    if failed:
        return False, f"{detail}; не прошли: {', '.join(failed)}"
    return True, detail


def _legacy_remaining_failures(health: object, repo: Path) -> tuple[bool, str]:
    quality = getattr(health, "_quality_contract", None)
    if callable(quality):
        return quality(Path(repo))

    legacy = getattr(health, "_v47_static_contract", None)
    if not callable(legacy):
        return True, "legacy static contract отсутствует; active contract authoritative"
    ok, detail = legacy(Path(repo))
    if ok:
        return True, str(detail)
    prefix = "v4.8-контракты не прошли: "
    raw = str(detail)
    if not raw.startswith(prefix):
        return False, raw
    superseded = {
        "worker-package-cancel-root",
        "worker-runtime-sync",
        "long-form-direct-resilience",
    }
    failed = [item.strip() for item in raw[len(prefix):].split(",") if item.strip()]
    remaining = [item for item in failed if item not in superseded]
    if remaining:
        return False, "не прошли: " + ", ".join(remaining)
    return True, "historical worker/runtime checks superseded by active release"


def _release_static_contract(health: object, repo: Path) -> tuple[bool, str]:
    base_ok, base_detail = _legacy_remaining_failures(health, Path(repo))
    mono_ok, mono_detail = _monolithic_static_contract(Path(repo))
    worker_ok = bool(WORKER_RUNTIME.startswith("dub-worker-quality-v"))
    detail = f"worker {WORKER_RUNTIME}; {base_detail}; {mono_detail}"
    return bool(base_ok and mono_ok and worker_ok), detail


def _patch_health() -> None:
    try:
        import handlers.dub_health as health
    except Exception:
        return

    original = health.collect_dub_health
    if getattr(original, "_active_monolithic_release", False):
        return

    def wrapped() -> list[dict[str, Any]]:
        checks = original()
        repo = Path(__file__).resolve().parents[2]
        title_ok, title_detail = _legacy._title_health_contract(repo)
        release_ok, release_detail = _release_static_contract(health, repo)
        for item in checks:
            if item.get("label") == "Clean Expressive NoChew + независимый QA":
                item["ok"] = bool(item.get("ok")) and title_ok and release_ok
                item["detail"] = "; ".join(
                    value
                    for value in (
                        str(item.get("detail") or ""),
                        title_detail,
                        release_detail,
                    )
                    if value
                )
                break
        return checks

    wrapped._canonical_media_title = True  # type: ignore[attr-defined]
    wrapped._active_monolithic_release = True  # type: ignore[attr-defined]
    health.collect_dub_health = wrapped
    legacy_health = getattr(health, "_legacy", None)
    if legacy_health is not None:
        legacy_health.collect_dub_health = wrapped


_legacy._patch_health = _patch_health

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {
        "RELEASE_CONTRACT_POLICY",
        "_legacy_remaining_failures",
        "_monolithic_static_contract",
        "_patch_health",
        "_release_static_contract",
    }
)
