from __future__ import annotations

from pathlib import Path
import runpy
import sys
from typing import NamedTuple

import dotenv
import pytest

# Import the heavy production module under the real interpreter before tests
# emulate the supported version gate. bot_new still executes its own import and
# complete bootstrap; source-order contracts cover the first-import ordering.
import main
import services
import services.bgutil_orphan_recovery as bgutil_recovery
import services.bot_lifecycle as bot_lifecycle
import services.database_migrations as database_migrations
import services.process_singleton as process_singleton
import services.runtime_dependency_lock as dependency_lock
import services.runtime_manifest as runtime_manifest
import services.youtube_po_token_runtime as po_token_runtime
import tools.ensure_bgutil_provider as bgutil_provider


ROOT = Path(__file__).resolve().parents[1]
BOT_NEW = ROOT / "bot_new.py"
GEMINI_KEY_NAMES = ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4")


class _SupportedVersion(NamedTuple):
    major: int
    minor: int
    micro: int
    releaselevel: str
    serial: int


class _OfflinePoRuntime:
    def status_text(self) -> str:
        return "offline-orchestration-double"


def _install_bootstrap_doubles(monkeypatch: pytest.MonkeyPatch, *, singleton_available: bool = True) -> list[str]:
    """Install boundary doubles; these tests make no external readiness claim."""

    events: list[str] = []
    monkeypatch.setattr(sys, "version_info", _SupportedVersion(3, 12, 0, "final", 0))
    monkeypatch.setenv("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    monkeypatch.setenv("BOT_TOKEN", "offline-bootstrap-test-token")
    for name in GEMINI_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda: False)

    def _validate(root: Path, *, skip_names: tuple[str, ...]) -> dict[str, int]:
        assert root == ROOT
        assert skip_names == ("google-genai",)
        events.append("dependencies")
        return {"checked": 0}

    def _acquire() -> bool:
        events.append("singleton")
        return singleton_available

    def _recover() -> None:
        events.append("orphan_recovery")
        return None

    def _ensure_provider() -> None:
        events.append("provider_source")

    def _require_po_runtime() -> _OfflinePoRuntime:
        events.append("po_runtime")
        return _OfflinePoRuntime()

    def _pre_main() -> None:
        events.append("pre_main")

    def _apply_migrations(module: object) -> None:
        assert module is main
        events.append("migrations")

    def _post_main(module: object) -> None:
        assert module is main
        events.append("post_main")

    def _require_ready() -> None:
        events.append("require_ready")

    def _diagnostics() -> None:
        events.append("diagnostics")

    def _run_process(module: object) -> int:
        assert module is main
        events.append("lifecycle")
        return 0

    monkeypatch.setattr(dependency_lock, "validate_runtime_dependency_lock", _validate)
    monkeypatch.setattr(process_singleton, "acquire_early_singleton", _acquire)
    monkeypatch.setattr(bgutil_recovery, "recover_orphaned_bgutil_http_runtime", _recover)
    monkeypatch.setattr(bgutil_provider, "ensure_bgutil_provider", _ensure_provider)
    monkeypatch.setattr(po_token_runtime, "require_youtube_po_token_runtime", _require_po_runtime)
    monkeypatch.setattr(runtime_manifest, "bootstrap_pre_main", _pre_main)
    monkeypatch.setattr(database_migrations, "apply_database_migrations", _apply_migrations)
    monkeypatch.setattr(runtime_manifest, "bootstrap_post_main", _post_main)
    monkeypatch.setattr(runtime_manifest, "require_runtime_ready", _require_ready)
    monkeypatch.setattr(services, "emit_service_bootstrap_diagnostics", _diagnostics)
    monkeypatch.setattr(bot_lifecycle, "run_bot_process", _run_process)
    return events


def test_bot_new_direct_entry_executes_ordered_offline_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _install_bootstrap_doubles(monkeypatch)

    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(BOT_NEW), run_name="__main__")

    assert caught.value.code == 0
    assert events == [
        "dependencies",
        "singleton",
        "orphan_recovery",
        "provider_source",
        "po_runtime",
        "pre_main",
        "migrations",
        "post_main",
        "require_ready",
        "diagnostics",
        "lifecycle",
    ]


def test_bot_new_singleton_conflict_stops_before_child_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _install_bootstrap_doubles(monkeypatch, singleton_available=False)

    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(BOT_NEW), run_name="__main__")

    assert caught.value.code == 2
    assert events == ["dependencies", "singleton"]


def test_bot_new_missing_token_stops_before_dependency_or_provider_work(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _install_bootstrap_doubles(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "")

    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(BOT_NEW), run_name="__main__")

    assert caught.value.code == 1
    assert events == []
