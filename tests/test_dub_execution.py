from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

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
