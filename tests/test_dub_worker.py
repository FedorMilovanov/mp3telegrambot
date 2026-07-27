from __future__ import annotations

from pathlib import Path

import pytest

import tools.voxcpm2.dub_worker as worker


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
