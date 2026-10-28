from __future__ import annotations

import pytest

from services import process_executables


def test_required_executable_uses_resolved_path(monkeypatch) -> None:
    monkeypatch.setattr(process_executables.shutil, "which", lambda name: f"/resolved/{name}")

    assert process_executables.resolve_required_executable("taskkill") == "/resolved/taskkill"


def test_required_executable_preserves_missing_file_error(monkeypatch) -> None:
    monkeypatch.setattr(process_executables.shutil, "which", lambda _name: None)

    with pytest.raises(FileNotFoundError, match="taskkill"):
        process_executables.resolve_required_executable("taskkill")
