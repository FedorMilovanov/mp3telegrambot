#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility facade preserving Dub title-policy monkeypatch semantics.

The canonical title implementation remains in ``services/dub_title_policy.py``.
Package facades keep legacy functions for stability, but those functions resolve
globals in their original modules. After the normal title-policy patch runs,
this facade mirrors the health wrapper into the legacy health module and makes
worker health follow the shared release identity instead of stale version text.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from services.dub_worker_release import (
    INDEPENDENT_QA_RECOVERY_POLICY,
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
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_legacy_patch_health = _legacy._patch_health


def _release_label() -> str:
    value = str(WORKER_RUNTIME)
    return value.rsplit("v", 1)[-1] if "v" in value else value


def _release_static_contract(health: object, repo: Path) -> tuple[bool, str]:
    """Preserve all old static gates, replacing only superseded release checks."""
    label = _release_label()
    ok, detail = health._v47_static_contract(Path(repo))
    if ok:
        return True, str(detail).replace("worker v4.8", f"worker v{label}")

    prefix = "v4.8-контракты не прошли: "
    raw = str(detail)
    if not raw.startswith(prefix):
        return False, raw
    failed = [item.strip() for item in raw[len(prefix) :].split(",") if item.strip()]
    superseded = {"worker-package-cancel-root", "worker-runtime-sync"}
    remaining = [item for item in failed if item not in superseded]

    repo = Path(repo)
    release = repo / "services" / "dub_worker_release.py"
    worker_main = (
        repo / "tools" / "voxcpm2" / "dub_worker_hardened" / "__main__.py"
    )
    supervisor = repo / "services" / "dub_studio_runtime" / "__init__.py"
    direct_main = (
        repo / "tools" / "voxcpm2" / "generic_clean_direct_runtime" / "__main__.py"
    )
    recovery = repo / "tools" / "voxcpm2" / "independent_qa_retry.py"
    release_text = release.read_text(encoding="utf-8") if release.is_file() else ""
    worker_text = worker_main.read_text(encoding="utf-8") if worker_main.is_file() else ""
    supervisor_text = supervisor.read_text(encoding="utf-8") if supervisor.is_file() else ""
    direct_main_text = direct_main.read_text(encoding="utf-8") if direct_main.is_file() else ""
    recovery_text = recovery.read_text(encoding="utf-8") if recovery.is_file() else ""

    release_ok = all(
        marker in release_text
        for marker in (
            f'WORKER_RUNTIME = "{WORKER_RUNTIME}"',
            'RELEASE_POLICY = "single-source-worker-release-identity-v1"',
            'PREFLIGHT_TRANSPORT_POLICY = "marked-preflight-json-transport-v1"',
            f'INDEPENDENT_QA_RECOVERY_POLICY = "{INDEPENDENT_QA_RECOVERY_POLICY}"',
        )
    )
    worker_ok = all(
        marker in worker_text
        for marker in (
            "from services.dub_worker_release import WORKER_RUNTIME",
            "def activate_release_identity(",
            "package._RUNTIME_VERSION = WORKER_RUNTIME",
            "_legacy._RUNTIME_VERSION = WORKER_RUNTIME",
            "activate_release_identity()",
            "install_preflight_json()",
            "main()",
        )
    )
    supervisor_ok = all(
        marker in supervisor_text
        for marker in (
            "from services.dub_worker_release import WORKER_RUNTIME",
            "_WORKER_RUNTIME = WORKER_RUNTIME",
            "_legacy._WORKER_RUNTIME = _WORKER_RUNTIME",
            "class _WriteThroughModule",
            "_module.__class__ = _WriteThroughModule",
        )
    )
    recovery_ok = all(
        marker in recovery_text
        for marker in (
            f'POLICY = "{INDEPENDENT_QA_RECOVERY_POLICY}"',
            "MAX_RECOVERY_CYCLES = 3",
            "INTERNAL_SEED_ROUNDS_PER_CALL = 2",
            "def _retry_context(",
            "def _retarget_checkpoints(",
            "failed_ids=failed_ids",
            "next_request[\"base_seed\"] = next_base_seed",
            "def install(",
        )
    ) and all(
        marker in direct_main_text
        for marker in (
            "from tools.voxcpm2 import independent_qa_retry",
            "independent_qa_retry.install()",
            "main()",
        )
    )
    if not (release_ok and worker_ok and supervisor_ok and recovery_ok):
        remaining.append("worker-current-release")
    if remaining:
        return False, f"v{label}-контракты не прошли: " + ", ".join(remaining)
    return True, (
        f"worker v{label}/preflight v2; shared release identity; stale-worker replacement; "
        "marked noise-tolerant JSON transport; bounded report-backed segment-only independent "
        "QA recovery; cancellation, explicit root and job-level quality restarts; все прежние "
        "cadence/tail/fit/checkpoint/post-AAC gates активны"
    )


def _patch_health() -> None:
    _legacy_patch_health()
    try:
        import handlers.dub_health as health
    except Exception:
        return

    legacy_health = getattr(health, "_legacy", None)
    wrapped = getattr(health, "collect_dub_health", None)
    if legacy_health is None or not callable(wrapped):
        return

    # Live worker status and static contracts must use the same release marker.
    health._WORKER_RUNTIME = WORKER_RUNTIME
    legacy_health._WORKER_RUNTIME = WORKER_RUNTIME

    def release_aware_quality_contract(repo: Path) -> tuple[bool, str]:
        base_ok, base_detail = health._legacy_quality_without_superseded_worker(repo)
        release_ok, release_detail = _release_static_contract(health, repo)
        supplemental_ok, supplemental_detail = health._supplemental_quality_contract(repo)
        detail = "; ".join((base_detail, release_detail, supplemental_detail))
        return bool(base_ok and release_ok and supplemental_ok), detail

    health._quality_contract = release_aware_quality_contract
    legacy_health._quality_contract = release_aware_quality_contract
    legacy_health.collect_dub_health = wrapped


# install_dub_title_policy resolves this global in the legacy module at call time.
_legacy._patch_health = _patch_health

install_dub_title_policy = _legacy.install_dub_title_policy
install_voxcpm_title_policy = _legacy.install_voxcpm_title_policy
canonical_delivery_filename = _legacy.canonical_delivery_filename
canonical_media_title = _legacy.canonical_media_title
RU_SERVICE_WORDS = _legacy.RU_SERVICE_WORDS

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {"_patch_health", "_release_static_contract"}
)
