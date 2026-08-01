from __future__ import annotations

from pathlib import Path

import pytest

from tools.check_requirements_lock import POLICY, validate_lock


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_repository_lock_covers_all_direct_requirements():
    result = validate_lock()
    assert result["policy"] == POLICY
    assert result["direct_count"] > 0
    assert result["locked_count"] >= result["direct_count"]


def test_missing_direct_package_fails(tmp_path: Path):
    direct = tmp_path / "requirements.txt"
    lock = tmp_path / "requirements-lock.txt"
    _write(direct, "requests>=2\npython-dotenv>=1\n")
    _write(lock, "requests==2.34.2\n")

    with pytest.raises(RuntimeError, match="python-dotenv"):
        validate_lock(direct_paths=(direct,), lock_path=lock)


def test_non_exact_or_duplicate_lock_entry_fails(tmp_path: Path):
    direct = tmp_path / "requirements.txt"
    lock = tmp_path / "requirements-lock.txt"
    _write(direct, "requests>=2\n")
    _write(lock, "requests>=2\n")
    with pytest.raises(RuntimeError, match="exact == pin"):
        validate_lock(direct_paths=(direct,), lock_path=lock)

    _write(lock, "requests==2.34.2\nRequests==2.34.2\n")
    with pytest.raises(RuntimeError, match="Duplicate"):
        validate_lock(direct_paths=(direct,), lock_path=lock)


def test_locked_version_must_satisfy_direct_range(tmp_path: Path):
    direct = tmp_path / "requirements.txt"
    lock = tmp_path / "requirements-lock.txt"
    _write(direct, "requests>=3\n")
    _write(lock, "requests==2.34.2\n")

    with pytest.raises(RuntimeError, match="violate"):
        validate_lock(direct_paths=(direct,), lock_path=lock)
