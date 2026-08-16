from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tools.voxcpm2 import dub_worker_hardened as hardened


@pytest.mark.parametrize("store", [object(), type("Store", (), {"root": ""})(), type("Store", (), {"root": True})()])
def test_store_root_environment_rejects_missing_empty_and_bool_roots(store: Any) -> None:
    with pytest.raises(RuntimeError, match="store root"):
        with hardened._store_root_environment(store):
            raise AssertionError("invalid store root must never be entered")


class RootlessJobStore:
    def __init__(self, tmp_path: Path) -> None:
        self.logs_dir = tmp_path / "logs"
        self.finished: list[dict[str, Any]] = []
        self.log_path: Path | None = None

    def is_cancel_requested(self, _job_id: int) -> bool:
        return False

    def get_project(self, _project_id: str) -> dict[str, Any]:
        raise AssertionError("project lookup must not run without store.root")

    def set_job_log_path(self, _job_id: int, path: Path) -> None:
        self.log_path = Path(path)

    def finish_job(self, job_id: int, **kwargs: Any) -> None:
        self.finished.append({"job_id": job_id, **kwargs})


def test_rootless_worker_job_fails_before_project_or_runner(
    monkeypatch,
    tmp_path: Path,
) -> None:
    store = RootlessJobStore(tmp_path)
    runner_calls: list[int] = []
    hardened.worker._STOP.clear()
    monkeypatch.setattr(
        hardened,
        "_ORIGINAL_EXECUTE_JOB",
        lambda *_args, **_kwargs: runner_calls.append(1),
    )

    hardened._execute_job_with_cancellable_preflight(
        store,
        "worker-1",
        {
            "id": 77,
            "project_id": "dub-0123456789",
            "action": "render_direct",
        },
    )

    assert runner_calls == []
    assert store.finished[-1]["status"] == "failed"
    assert "store root" in store.finished[-1]["error"]
    assert store.log_path == tmp_path / "logs" / "job-000077.log"
    assert "store root" in store.log_path.read_text(encoding="utf-8")
