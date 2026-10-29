"""Direct behavior tests for Dub Studio local health preflight."""

import asyncio
from types import SimpleNamespace

import pytest

import core.media_title_policy as title_policy
import handlers.dub_health as health
import services.dub_release_health_v64 as release_health
from services import dub_studio_runtime


@pytest.fixture(autouse=True)
def _fresh_worker_contract(monkeypatch):
    monkeypatch.setattr(
        health,
        "worker_is_fresh",
        lambda worker: bool(worker and worker.get("fresh")),
    )


def _worker(*, status="idle", fresh=False, current=False):
    return {
        "status": status,
        "pid": 123,
        "fresh": fresh,
        "details": {"runtime": health._WORKER_RUNTIME} if current else {},
    }


def test_worker_currency_requires_freshness_and_current_runtime():
    assert health._worker_is_current(_worker(fresh=True, current=True)) is True
    assert health._worker_is_current(_worker(fresh=True, current=False)) is False
    assert health._worker_is_current(_worker(fresh=False, current=True)) is False
    assert health._worker_is_current(None) is False


def test_worker_snapshot_does_not_repair_current_or_busy_workers(monkeypatch):
    current = _worker(fresh=True, current=True)
    busy = _worker(status="busy")
    workers = iter((current, busy))

    class Store:
        def latest_worker(self):
            return next(workers)

    monkeypatch.setattr(health, "DubStore", Store)

    def repair_must_not_run():
        raise AssertionError("current and busy workers need no repair")

    monkeypatch.setattr(dub_studio_runtime, "ensure_worker_running", repair_must_not_run)

    assert health._worker_snapshot_with_repair() is current
    assert health._worker_snapshot_with_repair() is busy


def test_worker_snapshot_returns_stale_worker_when_repair_is_not_requested(monkeypatch):
    stale = _worker()

    class Store:
        def latest_worker(self):
            return stale

    monkeypatch.setattr(health, "DubStore", Store)
    monkeypatch.setattr(dub_studio_runtime, "ensure_worker_running", lambda: False)
    assert health._worker_snapshot_with_repair() is stale

    def fail_repair():
        raise RuntimeError("cannot request worker")

    monkeypatch.setattr(dub_studio_runtime, "ensure_worker_running", fail_repair)
    assert health._worker_snapshot_with_repair() is stale


def test_worker_snapshot_polls_until_repaired_worker_is_current(monkeypatch):
    stale = _worker()
    current = _worker(fresh=True, current=True)
    workers = iter((stale, stale, current))

    class Store:
        def latest_worker(self):
            return next(workers)

    sleeps = []
    monkeypatch.setattr(health, "DubStore", Store)
    monkeypatch.setattr(dub_studio_runtime, "ensure_worker_running", lambda: True)
    monkeypatch.setattr(health.time, "sleep", sleeps.append)

    assert health._worker_snapshot_with_repair() is current
    assert sleeps == [0.1]


def test_worker_snapshot_has_bounded_polling_and_returns_last_snapshot(monkeypatch):
    stale = _worker()

    class Store:
        def __init__(self):
            self.calls = 0

        def latest_worker(self):
            self.calls += 1
            return stale

    store = Store()
    sleeps = []
    monkeypatch.setattr(health, "DubStore", lambda: store)
    monkeypatch.setattr(dub_studio_runtime, "ensure_worker_running", lambda: True)
    monkeypatch.setattr(health.time, "sleep", sleeps.append)

    assert health._worker_snapshot_with_repair() is stale
    assert store.calls == 31
    assert sleeps == [0.1] * 30


def _empty_quality_repo(root):
    relatives = (
        "tools/voxcpm2/clean_runtime_contract.py",
        "tools/voxcpm2/clean_production_core.py",
        "tools/voxcpm2/clean_source_download.py",
        "tools/voxcpm2/clean_request_settings.py",
        "tools/voxcpm2/strict_translation_payload.py",
        "tools/voxcpm2/generic_gemini_runtime.py",
        "tools/voxcpm2/generic_direct_runtime.py",
        "tools/voxcpm2/generic_custom_runtime.py",
        "tools/voxcpm2/generic_clean_audio_repair_runtime.py",
        "tools/voxcpm2/semantic_block_runtime.py",
        "tools/voxcpm2/direct_max_quality_io.py",
        "tools/voxcpm2/direct_retry_epoch.py",
        "tools/voxcpm2/master_direct_russian_only.py",
        "tools/voxcpm2/dub_job_preflight.py",
        "services/speech_backends/voxcpm2.py",
        "services/dub_worker.py",
    )
    for relative in relatives:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return root


def test_quality_contract_reports_missing_canonical_owners(tmp_path):
    ok, detail = health._quality_contract(tmp_path)

    assert ok is False
    assert detail.startswith("не найдены canonical owners: ")
    assert "backend" in detail
    assert "worker" in detail


def test_quality_contract_fails_closed_on_all_invalid_contracts(tmp_path, monkeypatch):
    repo = _empty_quality_repo(tmp_path)
    monkeypatch.setattr(
        health,
        "_read",
        lambda path: "sys.modules[" if path.name == "generic_gemini_runtime.py" else "",
    )
    recipe = SimpleNamespace(recipe_id="broken", action=lambda _action: {})
    monkeypatch.setattr(health, "load_recipe", lambda _recipe_id: recipe)

    ok, detail = health._quality_contract(repo)

    assert ok is False
    for failure in (
        "runtime-safety",
        "recipe-routing",
        "direct-master",
        "fingerprints",
        "preflight",
        "worker",
        "direct-runtime",
    ):
        assert failure in detail


def test_quality_contract_converts_recipe_errors_to_failed_routing(tmp_path, monkeypatch):
    repo = _empty_quality_repo(tmp_path)
    monkeypatch.setattr(health, "_read", lambda _path: "")

    def fail_recipe(_recipe_id):
        raise RuntimeError("recipe unavailable")

    monkeypatch.setattr(health, "load_recipe", fail_recipe)

    ok, detail = health._quality_contract(repo)

    assert ok is False
    assert "recipe-routing" in detail


def test_recipe_checks_return_three_failed_checks_on_error(monkeypatch):
    def fail_recipe(_recipe_id):
        raise RuntimeError("recipe boom")

    monkeypatch.setattr(health, "load_recipe", fail_recipe)

    checks = health._recipe_checks()

    assert len(checks) == 3
    assert [check["ok"] for check in checks] == [False, False, False]
    assert {check["detail"] for check in checks} == {"recipe boom"}


def _collect_dependencies(monkeypatch, tmp_path, *, available):
    monkeypatch.setattr(
        health,
        "_recipe_checks",
        lambda: [health._check("Recipe", available, "recipe")],
    )
    monkeypatch.setattr(health, "_quality_contract", lambda _repo: (available, "quality"))
    monkeypatch.setattr(title_policy, "media_title_policy_contract", lambda: (available, "title"))
    monkeypatch.setattr(release_health, "_v68_quality_contract", lambda _repo: (available, "release"))
    monkeypatch.setattr(
        health.importlib.util,
        "find_spec",
        lambda _name: object() if available else None,
    )
    monkeypatch.setattr(
        health.shutil,
        "which",
        lambda binary: f"/bin/{binary}" if available else None,
    )

    cpu = tmp_path / ("cpu" if available else "missing-cpu")
    if available:
        for relative in ("bin/python", "Scripts/python.exe"):
            path = cpu / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
    archive = tmp_path / ("archive" if available else "missing-archive")
    if available:
        archive.mkdir()
    monkeypatch.setenv("DUB_CPU_VENV", str(cpu))
    monkeypatch.setenv("DUB_VOX_ARCHIVE", str(archive))
    monkeypatch.setenv("DUB_TTS_QA_MODEL", "" if available else " large ")

    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"):
        monkeypatch.delenv(name, raising=False)
    if available:
        monkeypatch.setenv("GEMINI_API_KEY", " one ")
        monkeypatch.setenv("GEMINI_API_KEY_3", "three")

    if available:
        storage = tmp_path / "storage"
    else:
        storage = tmp_path / "storage-file"
        storage.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(health, "studio_root", lambda: storage)

    if available:
        monkeypatch.setattr(
            health,
            "_worker_snapshot_with_repair",
            lambda: _worker(fresh=True, current=True),
        )
    else:

        def fail_worker():
            raise RuntimeError("worker down")

        monkeypatch.setattr(health, "_worker_snapshot_with_repair", fail_worker)

    monkeypatch.setattr(
        health,
        "sys",
        SimpleNamespace(stdout=SimpleNamespace(encoding="UTF-8" if available else None)),
    )
    if available:
        monkeypatch.setenv("PYTHONUTF8", "0")
    else:
        monkeypatch.delenv("PYTHONUTF8", raising=False)
    return storage


def test_collect_dub_health_reports_available_environment(tmp_path, monkeypatch):
    storage = _collect_dependencies(monkeypatch, tmp_path, available=True)

    checks = health.collect_dub_health()
    by_label = {check["label"]: check for check in checks}

    assert len(checks) == 12
    assert all(check["ok"] for check in checks)
    assert "quality; title; release;" in by_label["Clean Expressive NoChew + независимый QA"]["detail"]
    assert by_label["Whisper semantic QA"]["detail"].endswith("model=small")
    assert by_label["SoundFile WAV I/O"]["detail"] == "soundfile есть"
    assert by_label["ffmpeg"]["detail"] == "/bin/ffmpeg"
    assert by_label["ffprobe"]["detail"] == "/bin/ffprobe"
    assert by_label["Gemini MAX keys"]["detail"] == "GEMINI_API_KEY, GEMINI_API_KEY_3"
    assert by_label["Worker"]["ok"] is True
    assert by_label["Python UTF-8"]["ok"] is True
    assert not (storage / ".dub-health-write-test").exists()


def test_collect_dub_health_reports_missing_dependencies_and_io_errors(tmp_path, monkeypatch):
    _collect_dependencies(monkeypatch, tmp_path, available=False)

    checks = health.collect_dub_health()
    by_label = {check["label"]: check for check in checks}

    assert len(checks) == 12
    assert all(check["ok"] is False for check in checks)
    assert "faster-whisper=не найден; model=large" == by_label["Whisper semantic QA"]["detail"]
    assert by_label["SoundFile WAV I/O"]["detail"] == "soundfile не найден"
    assert by_label["ffmpeg"]["detail"] == "не найден в PATH"
    assert by_label["Gemini MAX keys"]["detail"] == "ключи не найдены"
    assert "storage-file" in by_label["Dub Studio storage"]["detail"]
    assert by_label["Worker"]["detail"] == "worker down"
    assert by_label["Python UTF-8"]["detail"] == "stdout=None; PYTHONUTF8="


class Message:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


def _update(*, user_id=1, with_user=True, with_message=True):
    message = Message() if with_message else None
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id) if with_user else None,
        effective_message=message,
    )
    return update, message


def test_health_admin_gate_handles_admin_denied_and_missing_user(monkeypatch):
    monkeypatch.setattr(health, "ADMIN_IDS", {1})
    update, _ = _update(user_id=1)
    assert asyncio.run(health._admin(update)) is True

    update, message = _update(user_id=2)
    assert asyncio.run(health._admin(update)) is False
    assert message.replies == [("⛔ /dubcheck доступна только администратору.", {})]

    update, _ = _update(with_user=False, with_message=False)
    assert asyncio.run(health._admin(update)) is False


def test_dubcheck_command_renders_escaped_bounded_health_report(monkeypatch):
    monkeypatch.setattr(health, "ADMIN_IDS", {1})
    checks = [
        {"label": "<ok>", "detail": "<" + "x" * 1000, "ok": True},
        {"label": "bad&", "detail": "missing", "ok": False},
    ]
    monkeypatch.setattr(health, "collect_dub_health", lambda: checks)
    update, message = _update()

    asyncio.run(health.dubcheck_command(update, SimpleNamespace()))

    text, kwargs = message.replies[0]
    assert "Пройдено: <b>1/2</b>" in text
    assert "✅ <b>&lt;ok&gt;</b>" in text
    assert "❌ <b>bad&amp;</b>" in text
    assert "&lt;" + "x" * 899 in text
    assert "x" * 900 not in text
    assert "Только Gemini MAX" in text
    assert kwargs == {"parse_mode": "HTML"}


def test_dubcheck_command_returns_after_admin_rejection(monkeypatch):
    monkeypatch.setattr(health, "ADMIN_IDS", set())

    def health_must_not_run():
        raise AssertionError("unauthorized health checks must not run")

    monkeypatch.setattr(health, "collect_dub_health", health_must_not_run)
    update, message = _update(user_id=2)

    asyncio.run(health.dubcheck_command(update, SimpleNamespace()))

    assert len(message.replies) == 1
    assert message.replies[0][0].startswith("⛔")


def test_dubcheck_command_handles_missing_effective_message(monkeypatch):
    monkeypatch.setattr(health, "ADMIN_IDS", {1})
    monkeypatch.setattr(health, "collect_dub_health", lambda: [])
    update, message = _update(with_message=False)

    asyncio.run(health.dubcheck_command(update, SimpleNamespace()))

    assert message is None


def test_health_registration_is_idempotent():
    class Application:
        def __init__(self):
            self.bot_data = {}
            self.handlers = []

        def add_handler(self, handler):
            self.handlers.append(handler)

    application = Application()
    health.register_dub_health_handler(application)
    health.register_dub_health_handler(application)

    assert len(application.handlers) == 1
    assert application.bot_data["dub_studio_health_registered"] is True
    assert application.handlers[0].commands == frozenset({"dubcheck"})
    assert application.handlers[0].callback is health.dubcheck_command
