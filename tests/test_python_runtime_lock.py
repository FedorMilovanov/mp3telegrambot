from __future__ import annotations

from pathlib import Path

import pytest

from services import python_runtime_lock as runtime_lock


def _write_lock(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_require_python_runtime_lock_accepts_exact_installed_versions(
    monkeypatch,
    tmp_path: Path,
) -> None:
    lock = _write_lock(
        tmp_path / "requirements-lock.txt",
        ["Alpha_Pkg==1.2.3", "beta-pkg==4.5.6"],
    )
    installed = {"Alpha_Pkg": "1.2.3", "beta-pkg": "4.5.6"}
    monkeypatch.setattr(
        runtime_lock.metadata,
        "version",
        lambda name: installed[name],
    )

    runtime_lock.require_python_runtime_lock(lock)


def test_require_python_runtime_lock_reports_missing_and_version_drift(
    monkeypatch,
    tmp_path: Path,
) -> None:
    lock = _write_lock(
        tmp_path / "requirements-lock.txt",
        ["alpha==1.0.0", "beta==2.0.0"],
    )

    def _version(name: str) -> str:
        if name == "alpha":
            return "9.9.9"
        raise runtime_lock.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(runtime_lock.metadata, "version", _version)

    with pytest.raises(runtime_lock.PythonRuntimeLockError) as exc_info:
        runtime_lock.require_python_runtime_lock(lock)

    message = str(exc_info.value)
    assert "alpha: installed 9.9.9, expected 1.0.0" in message
    assert "beta: missing (expected 2.0.0)" in message


def test_lock_parser_rejects_non_exact_or_duplicate_entries(tmp_path: Path) -> None:
    non_exact = _write_lock(tmp_path / "non-exact.txt", ["alpha>=1.0"])
    with pytest.raises(runtime_lock.PythonRuntimeLockError):
        runtime_lock.locked_versions(non_exact)

    duplicate = _write_lock(
        tmp_path / "duplicate.txt",
        ["Alpha_Pkg==1.0", "alpha-pkg==1.0"],
    )
    with pytest.raises(runtime_lock.PythonRuntimeLockError):
        runtime_lock.locked_versions(duplicate)


def test_bot_entrypoint_validates_lock_before_third_party_imports() -> None:
    source = Path("bot_new.py").read_text(encoding="utf-8")
    check_pos = source.index("require_python_runtime_lock()")
    dotenv_pos = source.index("from dotenv import load_dotenv")
    provider_pos = source.index("from tools.ensure_bgutil_provider")

    assert check_pos < dotenv_pos < provider_pos
    assert 'Рекомендуемый запуск: & ".\\\\Start Bot.bat"' in source
    assert ".\\\\.venv\\\\Scripts\\\\python.exe bot_new.py" in source


def test_real_repository_lock_is_exact_and_contains_runtime_anchors() -> None:
    locked = runtime_lock.locked_versions(Path("requirements-lock.txt"))

    assert locked["yt-dlp"][1] == "2026.7.4"
    assert locked["google-genai"][1]
    assert locked["python-telegram-bot"][1]
    assert locked["ctranslate2"][1]
