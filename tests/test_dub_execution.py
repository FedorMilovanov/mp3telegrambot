from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import services.dub_execution as dub_execution
from services.dub_execution import (
    ARTIFACT_VALIDATION_POLICY,
    active_execution_leases,
    finish_execution_lease,
    record_execution_lease,
    recover_orphaned_executions,
    validate_recipe_outputs,
)
from services.dub_studio import DubStore, Recipe


def _recipe(outputs: dict) -> Recipe:
    return Recipe(
        recipe_id="test_recipe",
        title="Test",
        speaker="",
        source_url="",
        description="",
        work_root="",
        actions={"render": {"runner": "python_module"}},
        outputs=outputs,
        raw={},
    )


def test_execution_lease_records_child_identity_and_fingerprint(tmp_path: Path):
    store = DubStore(tmp_path)
    lease = record_execution_lease(
        store,
        job_id=12,
        worker_id="worker-1",
        runner_pid=os.getpid(),
        command=["python", "-m", "example"],
    )

    assert lease.runner_pid == os.getpid()
    assert len(lease.command_fingerprint) == 64
    active = active_execution_leases(store)
    assert [item.job_id for item in active] == [12]

    finish_execution_lease(store, 12, state="finished")
    assert active_execution_leases(store) == []


def test_live_lease_cannot_be_replaced(tmp_path: Path):
    store = DubStore(tmp_path)
    record_execution_lease(
        store,
        job_id=9,
        worker_id="worker-1",
        runner_pid=os.getpid(),
        command=["python", "one.py"],
    )

    with pytest.raises(RuntimeError, match="already has a live runner lease"):
        record_execution_lease(
            store,
            job_id=9,
            worker_id="worker-2",
            runner_pid=os.getpid(),
            command=["python", "two.py"],
        )


def test_recovery_marks_nonexistent_runner_stale_before_requeue(tmp_path: Path):
    store = DubStore(tmp_path)
    record_execution_lease(
        store,
        job_id=5,
        worker_id="old-worker",
        runner_pid=os.getpid(),
        command=["python", "render.py"],
    )
    with store.connect() as conn:
        conn.execute(
            "UPDATE dub_execution_leases SET runner_pid=?, process_start_token='' WHERE job_id=?",
            (999_999_999, 5),
        )
        conn.commit()

    assert recover_orphaned_executions(store) == 1
    assert active_execution_leases(store) == []


def test_required_artifacts_must_be_current_and_manifest_completed(tmp_path: Path):
    output = tmp_path / "output"
    output.mkdir()
    manifest = output / "manifest.json"
    video = output / "final_upload.mp4"
    manifest.write_text(json.dumps({"phase": "completed"}), encoding="utf-8")
    video.write_bytes(b"video" * 100)
    recipe = _recipe(
        {
            "manifest": {
                "path": "{work_root}/output/manifest.json",
                "required": True,
                "min_bytes": 10,
                "actions": ["render"],
            },
            "mixed": {
                "path": "{work_root}/output/final_upload.mp4",
                "required": True,
                "min_bytes": 100,
                "actions": ["render"],
            },
        }
    )

    report = validate_recipe_outputs(
        recipe,
        action_name="render",
        work_root=str(tmp_path),
        job_started_at="1970-01-01T00:00:00+00:00",
    )

    assert report["artifact_validation_policy"] == ARTIFACT_VALIDATION_POLICY
    assert report["outputs"]["manifest"]["valid"] is True
    assert report["outputs"]["mixed"]["valid"] is True


def test_stale_required_artifact_is_rejected(tmp_path: Path):
    output = tmp_path / "output"
    output.mkdir()
    artifact = output / "manifest.json"
    artifact.write_text(json.dumps({"phase": "completed"}), encoding="utf-8")
    os.utime(artifact, (1, 1))
    recipe = _recipe(
        {
            "manifest": {
                "path": "{work_root}/output/manifest.json",
                "required": True,
                "actions": ["render"],
            }
        }
    )

    with pytest.raises(RuntimeError, match="current-job artifacts"):
        validate_recipe_outputs(
            recipe,
            action_name="render",
            work_root=str(tmp_path),
            job_started_at="2026-01-01T00:00:00+00:00",
        )


def test_ffprobe_postcondition_uses_resolved_executable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls: dict[str, object] = {}

    def resolve(name: str) -> str:
        calls["executable_name"] = name
        return "/resolved/ffprobe"

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls["command"] = command
        calls["kwargs"] = kwargs
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(dub_execution, "resolve_required_executable", resolve)
    monkeypatch.setattr(dub_execution.subprocess, "run", run)
    media = tmp_path / "candidate.mp4"

    assert dub_execution._ffprobe_media_ok(media, require_video=True, require_audio=True) is False
    assert calls["executable_name"] == ("ffprobe.exe" if os.name == "nt" else "ffprobe")
    command = calls["command"]
    assert isinstance(command, list)
    assert command[0] == "/resolved/ffprobe"
    assert command[-1] == str(media)


def test_windows_pid_check_closes_owned_process_handle(monkeypatch: pytest.MonkeyPatch):
    opened: list[tuple[int, bool, int]] = []
    closed: list[int] = []

    def open_process(access: int, inherit: bool, pid: int) -> int:
        opened.append((access, inherit, pid))
        return 88

    kernel32 = SimpleNamespace(OpenProcess=open_process, CloseHandle=closed.append)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False)
    monkeypatch.setattr(dub_execution.os, "name", "nt")

    assert dub_execution._pid_is_running(123) is True
    assert opened == [(0x1000, False, 123)]
    assert closed == [88]


def test_windows_start_token_closes_handle_after_process_times(monkeypatch: pytest.MonkeyPatch):
    closed: list[int] = []
    calls: list[int] = []

    def get_process_times(handle: int, *_args: object) -> int:
        calls.append(handle)
        return 1

    kernel32 = SimpleNamespace(
        OpenProcess=lambda *_args: 77,
        GetProcessTimes=get_process_times,
        CloseHandle=closed.append,
    )
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False)

    assert dub_execution._windows_start_token(123) == "0"
    assert calls == [77]
    assert closed == [77]
