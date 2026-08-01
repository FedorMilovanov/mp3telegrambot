from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from services.dub_worker_release import WORKER_RUNTIME
from tools.voxcpm2 import dub_worker_hardened as hardened


class FakeStore:
    def __init__(self, tmp_path: Path, cancel_values: list[bool]) -> None:
        self.root = (tmp_path / "studio").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
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


def test_worker_package_exposes_current_cancellation_contract() -> None:
    assert Path(hardened.__file__).name == "__init__.py"
    assert hardened.CANCELLATION_POLICY == "preflight-cancel-before-runner-v1"
    assert hardened.STORE_ROOT_POLICY == "explicit-worker-root-propagation-v2"
    assert hardened.DELIVERY_RESILIENCE_POLICY == "cadence-tail-fit-adaptive-resume-v1"
    assert hardened.JOB_QUALITY_RETRY_POLICY == "worker-checkpoint-quality-restart-v1"
    assert hardened.MAX_JOB_QUALITY_RESTARTS == 3
    assert hardened._RUNTIME_VERSION == WORKER_RUNTIME
    assert hardened._legacy._RUNTIME_VERSION == WORKER_RUNTIME
    main_source = (Path(hardened.__file__).parent / "__main__.py").read_text(
        encoding="utf-8"
    )
    assert "from . import main" in main_source
    assert "main()" in main_source


def test_cancel_before_preflight_never_runs_probe_or_runner(
    monkeypatch: pytest.MonkeyPatch,
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
    monkeypatch: pytest.MonkeyPatch,
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
    assert [item["stage"] for item in store.progress] == ["preflight"]


def test_normal_preflight_starts_original_runner_once(
    monkeypatch: pytest.MonkeyPatch,
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


def test_store_root_is_scoped_to_preflight_and_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = FakeStore(tmp_path, [False, False])
    seen: list[str | None] = []
    previous = str((tmp_path / "previous-root").resolve())
    monkeypatch.setenv("DUB_STUDIO_ROOT", previous)
    hardened._legacy.worker._STOP.clear()

    def fake_preflight(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        seen.append(os.environ.get("DUB_STUDIO_ROOT"))
        return {"passed": True, "skipped": True}

    monkeypatch.setattr(hardened._legacy.dub_job_preflight, "run", fake_preflight)
    monkeypatch.setattr(
        hardened._legacy,
        "_ORIGINAL_EXECUTE_JOB",
        lambda *_args, **_kwargs: seen.append(os.environ.get("DUB_STUDIO_ROOT")),
    )

    hardened._execute_job_with_cancellable_preflight(store, "worker-1", _job())

    assert seen == [str(store.root), str(store.root)]
    assert os.environ["DUB_STUDIO_ROOT"] == previous


def test_real_preflight_failure_is_failed_and_logged(
    monkeypatch: pytest.MonkeyPatch,
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


def test_quality_failure_restarts_same_job_until_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = FakeStore(tmp_path, [False])
    calls: list[int] = []
    monkeypatch.delenv("DUB_WORKER_MAX_QUALITY_RESTARTS", raising=False)

    def fake_runner(capture: Any, _worker_id: str, job: dict[str, Any]) -> None:
        calls.append(len(calls) + 1)
        if len(calls) < 3:
            capture.finish_job(
                int(job["id"]),
                status="failed",
                error=(
                    "RuntimeError: Сегмент #12: нет ни одного hard-quality "
                    f"кандидата; следующий повтор использует seed epoch {len(calls) + 1}."
                ),
            )
            return
        capture.finish_job(
            int(job["id"]),
            status="succeeded",
            result={"output": "ready.mp4"},
        )

    monkeypatch.setattr(hardened._legacy, "_ORIGINAL_EXECUTE_JOB", fake_runner)
    hardened._run_with_quality_restarts(
        store,
        "worker-1",
        _job(),
        store.get_project(_job()["project_id"]),
    )

    assert calls == [1, 2, 3]
    assert store.finished[-1]["status"] == "succeeded"
    retries = [item["stage"] for item in store.progress if item["stage"].startswith("quality-retry:")]
    assert retries == ["quality-retry:1/3", "quality-retry:2/3"]


def test_install_preserves_legacy_hardening_and_overrides_execute_only(
    monkeypatch: pytest.MonkeyPatch,
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
        assert hardened._legacy._RUNTIME_VERSION == WORKER_RUNTIME
        assert hardened._legacy.worker.execute_job is (
            hardened._execute_job_with_cancellable_preflight
        )
    finally:
        hardened._legacy.worker.execute_job = original_execute
