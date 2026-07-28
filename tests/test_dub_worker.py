from __future__ import annotations

from pathlib import Path

import pytest

from services.dub_studio import DubStore
import tools.voxcpm2.dub_worker as worker
import tools.voxcpm2.dub_worker_hardened as hardened_worker


def test_worker_builds_only_registered_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "_powershell_executable", lambda: "pwsh")
    command, spec = worker.build_command(
        "john_piper_z20py4yqhyq",
        "repair_psalm15",
    )
    assert command[0] == "pwsh"
    assert "-File" in command
    script = Path(command[command.index("-File") + 1])
    assert script.suffix.lower() == ".ps1"
    assert "tools" in script.parts
    assert spec["kind"] == "repair"
    joined = " ".join(command)
    assert "-OriginalLevel 0.18" in joined
    assert "0.25" not in joined


def test_worker_rejects_invalid_recipe_action(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "_powershell_executable", lambda: "pwsh")
    with pytest.raises(KeyError):
        worker.build_command("john_piper_z20py4yqhyq", "arbitrary_shell")


def test_progress_parser_understands_segments_and_master() -> None:
    progress, stage = worker._progress_from_line("[2/5] #2 EXTENDED", 5)
    assert progress > 5
    assert stage == "segment 2/5"

    progress, stage = worker._progress_from_line(
        "=== 5. Постоянный микс и финальный master ===",
        progress,
    )
    assert stage == "5. Постоянный микс и финальный master"


def test_deepest_error_line_prefers_final_exception() -> None:
    error = "\n".join(
        [
            "Runner exited with code 1.",
            'File "generic_project_runtime.py", line 641, in _run_voxcpm_and_master',
            "RuntimeError: VoxCPM2 CPU-синтез завершился с кодом 1.",
            "ModuleNotFoundError: No module named 'broken_dependency'",
        ]
    )
    assert hardened_worker._deepest_error_line(error) == "ModuleNotFoundError: No module named 'broken_dependency'"


def test_hardened_worker_installs_tree_cancel_progress_and_version_marker(tmp_path: Path) -> None:
    original_terminate = worker._terminate_process
    original_progress_parser = worker._progress_from_line
    original_register = DubStore.register_worker
    original_heartbeat = DubStore.worker_heartbeat
    original_update_progress = DubStore.update_job_progress
    original_finish_job = DubStore.finish_job
    try:
        hardened_worker.install_hardening()
        assert worker._terminate_process is hardened_worker._terminate_process_tree
        assert worker._progress_from_line is hardened_worker._progress_from_line_v43
        assert DubStore.register_worker is hardened_worker._register_versioned_worker
        assert DubStore.worker_heartbeat is hardened_worker._heartbeat_versioned_worker
        assert DubStore.update_job_progress is hardened_worker._update_progress_with_milestones
        assert DubStore.finish_job is hardened_worker._finish_job_with_root_cause
        assert hardened_worker._RUNTIME_VERSION == "dub-worker-quality-v4.3"

        store = DubStore(tmp_path)
        store.register_worker(
            "worker-test",
            pid=123,
            status="idle",
            details={"python": "python.exe"},
        )
        assert store.latest_worker()["details"]["runtime"] == hardened_worker._RUNTIME_VERSION

        store.worker_heartbeat("worker-test", status="idle")
        idle = store.latest_worker()
        assert idle["details"]["runtime"] == hardened_worker._RUNTIME_VERSION

        store.worker_heartbeat(
            "worker-test",
            status="busy",
            current_job_id=7,
            details={"stage": "synthesis"},
        )
        busy = store.latest_worker()
        assert busy["details"]["runtime"] == hardened_worker._RUNTIME_VERSION
        assert busy["details"]["stage"] == "synthesis"
    finally:
        worker._terminate_process = original_terminate
        worker._progress_from_line = original_progress_parser
        DubStore.register_worker = original_register
        DubStore.worker_heartbeat = original_heartbeat
        DubStore.update_job_progress = original_update_progress
        DubStore.finish_job = original_finish_job
