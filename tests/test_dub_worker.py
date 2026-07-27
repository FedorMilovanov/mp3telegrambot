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


def test_hardened_worker_installs_tree_cancel_and_version_marker() -> None:
    original_terminate = worker._terminate_process
    original_register = DubStore.register_worker
    try:
        hardened_worker.install_hardening()
        assert worker._terminate_process is hardened_worker._terminate_process_tree
        assert DubStore.register_worker is hardened_worker._register_versioned_worker
        assert hardened_worker._RUNTIME_VERSION == "dub-worker-tree-cancel-v2"
    finally:
        worker._terminate_process = original_terminate
        DubStore.register_worker = original_register
