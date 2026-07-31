from __future__ import annotations

from pathlib import Path
from typing import Any

from handlers import dub_health
from services import dub_studio_runtime
from services import dub_title_policy


def test_supervisor_facade_is_active_without_health_import_order_dependency() -> None:
    assert Path(dub_studio_runtime.__file__).name == "__init__.py"
    assert dub_studio_runtime._WORKER_RUNTIME == "dub-worker-quality-v6.8"
    assert dub_studio_runtime._legacy._WORKER_RUNTIME == "dub-worker-quality-v6.8"


def test_supervisor_monkeypatch_assignments_reach_legacy_function_globals() -> None:
    original = dub_studio_runtime._undelivered_notification_events

    def sentinel(_store: Any, limit: int = 20) -> list[dict[str, Any]]:
        return [{"limit": limit}]

    try:
        dub_studio_runtime._undelivered_notification_events = sentinel
        assert dub_studio_runtime._legacy._undelivered_notification_events is sentinel
        assert dub_studio_runtime._undelivered_notification_events is sentinel
    finally:
        dub_studio_runtime._undelivered_notification_events = original
    assert dub_studio_runtime._legacy._undelivered_notification_events is original


def test_title_policy_mirrors_health_wrapper_into_legacy_module(monkeypatch) -> None:
    assert Path(dub_title_policy.__file__).name == "__init__.py"
    original_package = dub_health.collect_dub_health
    original_legacy = dub_health._legacy.collect_dub_health

    def wrapped() -> list[dict[str, Any]]:
        return [{"label": "sentinel", "ok": True, "detail": "write-through"}]

    def fake_legacy_patch() -> None:
        dub_health.collect_dub_health = wrapped

    monkeypatch.setattr(dub_title_policy, "_legacy_patch_health", fake_legacy_patch)
    try:
        dub_title_policy._patch_health()
        assert dub_health.collect_dub_health is wrapped
        assert dub_health._legacy.collect_dub_health is wrapped
        assert dub_health._legacy.collect_dub_health()[0]["label"] == "sentinel"
    finally:
        dub_health.collect_dub_health = original_package
        dub_health._legacy.collect_dub_health = original_legacy


def test_title_policy_installer_resolves_facade_health_patch() -> None:
    assert dub_title_policy._legacy._patch_health is dub_title_policy._patch_health
