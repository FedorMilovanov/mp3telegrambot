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

from services.dub_worker_release import WORKER_RUNTIME

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


def _release_static_contract(health: object, repo: Path) -> tuple[bool, str]:
    """Preserve all old static gates, replacing only superseded release checks."""
    ok, detail = health._v47_static_contract(Path(repo))
    if ok:
        return True, str(detail).replace("worker v4.8", "worker v4.9")

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
    release_text = release.read_text(encoding="utf-8") if release.is_file() else ""
    worker_text = worker_main.read_text(encoding="utf-8") if worker_main.is_file() else ""
    supervisor_text = supervisor.read_text(encoding="utf-8") if supervisor.is_file() else ""

    release_ok = all(
        marker in release_text
        for marker in (
            'WORKER_RUNTIME = "dub-worker-quality-v4.9"',
            'RELEASE_POLICY = "single-source-worker-release-identity-v1"',
            'PREFLIGHT_TRANSPORT_POLICY = "marked-preflight-json-transport-v1"',
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
    if not (release_ok and worker_ok and supervisor_ok):
        remaining.append("worker-release-v49")
    if remaining:
        return False, "v4.9-контракты не прошли: " + ", ".join(remaining)
    return True, (
        "worker v4.9/preflight v2; shared release identity; stale-worker replacement; "
        "marked noise-tolerant JSON transport; cancellation, explicit root and job-level "
        "quality restarts; все прежние cadence/tail/fit/checkpoint/post-AAC gates активны"
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
