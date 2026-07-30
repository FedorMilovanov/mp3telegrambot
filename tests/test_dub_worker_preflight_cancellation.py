from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tools.voxcpm2 import dub_worker_hardened as hardened


class FakeStore:
    def __init__(self, tmp_path: Path, cancel_values: list[bool]) -> None:
        self.logs_dir = tmp_path / "logs"
        self.cancel_values = list(cancel_values)
        self.progress: list[dict[str, Any]] = []
        self.finished: list[dict[str, Any]] = []
        self.log_paths: list[tuple[int, Path]] = []

    def is_cancel_requested(self, _job_id: int) -> bool:
        if not self.cancel_values:
            return False
        if len(self.cancel_values) == 1:
            return self.cancel_values[0]
        return self.cancel_values.pop(0)

    def get_project(self, project_id: str) -> dict[str, Any]:
        return {
            "id": project_id,
            "recipe_id": "generic_short_v1",
            "work_root": "",
        }

    def update_job_progress(self, job_id: int, **kwargs: Any) -> None:
        self.progress.append({"job_id": job_id, **kwargs})

    def finish_job(self, job_id: int, **kwargs: Any) -> None:
        self.finished.append({"job_id": job_id, **kwargs})

    def set_job_log_path(self, job_id: int, path: Path) -> None:
        self.log_paths.append((job_id, Path(path)))


def _job() -> dict[str, Any]:
    return {
        "id": 17,
        "project_id": "dub-0123456789",
        "action": "render_direct",
    }


def test_worker_import_and_module_execution_resolve_package_facade() -> None:
    assert Path(hardened.__file__).name == "__init__.py"
    assert hardened.CANCELLATION_POLICY == "preflight-cancel-before-runner-v1"
    assert hardened._RUNTIME_VERSION == "dub-worker-quality-v4.6"
    main_source = (Path(hardened.__file__).parent / "__main__.py").read_text(
        encoding="utf-8"
    )
    assert "from . import main" in main_source
    assert "main()" in main_source


def test_cancel_before_preflight_never_runs_probe_or_runner(
    monkeypatch,
    tmp_path: Path,
) -> None:
    store = FakeStore(tmp_path, [True])
    calls: list[str] = []
    hardened._legacy.worker._STOP.clear()
    monkeypatch.setattr(
        hardened._legacy.dub_job_preflight,
        "run",
        lambda *_args, **_kwargs: calls.append("preflight"),
    )
    monkeypatch.setattr(
        hardened._legacy,
        "_ORIGINAL_EXECUTE_JOB",
        lambda *_args, **_kwargs: calls.append("runner"),
    )

    hardened._execute_job_with_cancellable_preflight(store, "worker-1", _job())

    assert calls == []
    assert store.finished == [
        {
            "job_id": 17,
            "status": "cancelled",
            "error": "Остановлено пользователем.",
        }
    ]
    assert store.progress == []


def test_cancel_during_preflight_never_starts_runner(
    monkeypatch,
    tmp_path: Path,
) -> None:
    store = FakeStore(tmp_path, [False, True])
    calls: list[str] = []
    hardened._legacy.worker._STOP.clear()

    def fake_preflight(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append("preflight")
        return {"passed": True, "skipped": False}

    monkeypatch.setattr(hardened._legacy.dub_job_preflight, "run", fake_preflight)
    monkeypatch.setattr(
        hardened._legacy,
        "_ORIGINAL_EXECUTE_JOB",
        lambda *_args, **_kwargs: calls.append("runner"),
    )

    hardened._execute_job_with_cancellable_preflight(store, "worker-1", _job())

    assert calls == ["preflight"]
    assert store.finished[-1]["status"] == "cancelled"
    assert store.finished[-1]["error"] == "Остановлено пользователем."
    assert [item["stage"] for item in store.progress] == ["preflight"]


def test_normal_preflight_starts_original_runner_once(
    monkeypatch,
    tmp_path: Path,
) -> None:
    store = FakeStore(tmp_path, [False, False])
    calls: list[str] = []
    hardened._legacy.worker._STOP.clear()
    monkeypatch.setattr(
        hardened._legacy.dub_job_preflight,
        "run",
        lambda *_args, **_kwargs: {"passed": True, "skipped": False},
    )
    monkeypatch.setattr(
        hardened._legacy,
        "_ORIGINAL_EXECUTE_JOB",
        lambda _store, worker_id, job: calls.append(
            f"runner:{worker_id}:{job['id']}"
        ),
    )

    hardened._execute_job_with_cancellable_preflight(store, "worker-1", _job())

    assert calls == ["runner:worker-1:17"]
    assert store.finished == []
    assert [item["stage"] for item in store.progress] == [
        "preflight",
        "preflight:ok",
    ]


def test_real_preflight_failure_is_failed_and_logged(
    monkeypatch,
    tmp_path: Path,
) -> None:
    store = FakeStore(tmp_path, [False, False])
    calls: list[str] = []
    hardened._legacy.worker._STOP.clear()

    def fail(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("IMPORT_SENTINEL")

    monkeypatch.setattr(hardened._legacy.dub_job_preflight, "run", fail)
    monkeypatch.setattr(
        hardened._legacy,
        "_ORIGINAL_EXECUTE_JOB",
        lambda *_args, **_kwargs: calls.append("runner"),
    )

    hardened._execute_job_with_cancellable_preflight(store, "worker-1", _job())

    assert calls == []
    assert store.finished[-1]["status"] == "failed"
    assert "IMPORT_SENTINEL" in store.finished[-1]["error"]
    log_path = tmp_path / "logs" / "job-000017.log"
    assert store.log_paths == [(17, log_path)]
    assert "IMPORT_SENTINEL" in log_path.read_text(encoding="utf-8")


def test_worker_stop_during_preflight_is_cancelled_not_failed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    store = FakeStore(tmp_path, [False])
    calls: list[str] = []
    hardened._legacy.worker._STOP.clear()

    def stop_worker(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        hardened._legacy.worker._STOP.set()
        return {"passed": True, "skipped": False}

    monkeypatch.setattr(hardened._legacy.dub_job_preflight, "run", stop_worker)
    monkeypatch.setattr(
        hardened._legacy,
        "_ORIGINAL_EXECUTE_JOB",
        lambda *_args, **_kwargs: calls.append("runner"),
    )
    try:
        hardened._execute_job_with_cancellable_preflight(store, "worker-1", _job())
    finally:
        hardened._legacy.worker._STOP.clear()

    assert calls == []
    assert store.finished[-1]["status"] == "cancelled"
    assert store.finished[-1]["error"] == "Worker stopping."


def test_install_keeps_agent_hardening_then_overrides_only_execute_job(
    monkeypatch,
) -> None:
    calls: list[str] = []
    original_execute = hardened._legacy.worker.execute_job
    monkeypatch.setattr(
        hardened._legacy,
        "install_hardening",
        lambda: calls.append("agent-hardening"),
    )
    try:
        hardened.install_hardening()
        assert calls == ["agent-hardening"]
        assert (
            hardened._legacy.worker.execute_job
            is hardened._execute_job_with_cancellable_preflight
        )
    finally:
        hardened._legacy.worker.execute_job = original_execute
