#!/usr/bin/env python3
"""Fail-closed verification of the installed Python runtime against the repo lock.

This module intentionally uses only the Python standard library. ``bot_new.py``
imports it before any third-party package so a stale/global interpreter cannot
bootstrap enough of the application to look healthy and then fail later on a
runtime-specific dependency path.
"""
from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path


POLICY = "installed-python-runtime-lock-v1"
DEFAULT_LOCK = Path("requirements-lock.txt")
_EXACT_PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)==(?P<version>[^\s;]+)$"
)


class PythonRuntimeLockError(RuntimeError):
    """Raised when the active interpreter does not match the repository lock."""


def _canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name or "").strip()).lower()


def locked_versions(lock_path: Path = DEFAULT_LOCK) -> dict[str, tuple[str, str]]:
    """Return canonical-name -> (distribution spelling, exact version)."""
    path = Path(lock_path)
    try:
        raw_lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise PythonRuntimeLockError(f"cannot read {path}: {exc}") from exc

    result: dict[str, tuple[str, str]] = {}
    for line_number, raw in enumerate(raw_lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _EXACT_PIN.fullmatch(line)
        if not match:
            raise PythonRuntimeLockError(
                f"{path}:{line_number} is not one exact distribution==version pin: {line!r}"
            )
        distribution = match.group("name")
        version = match.group("version")
        canonical = _canonical_name(distribution)
        if canonical in result:
            raise PythonRuntimeLockError(
                f"duplicate locked distribution {distribution!r} in {path}"
            )
        result[canonical] = (distribution, version)

    if not result:
        raise PythonRuntimeLockError(f"{path} contains no exact package pins")
    return result


def runtime_drift(lock_path: Path = DEFAULT_LOCK) -> list[str]:
    """Describe missing or version-drifted locked distributions."""
    drift: list[str] = []
    for _canonical, (distribution, expected) in locked_versions(lock_path).items():
        try:
            installed = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            drift.append(f"{distribution}: missing (expected {expected})")
            continue
        except Exception as exc:
            drift.append(
                f"{distribution}: metadata error {type(exc).__name__}: {exc}"
            )
            continue
        if installed != expected:
            drift.append(f"{distribution}: installed {installed}, expected {expected}")
    return drift


def require_python_runtime_lock(lock_path: Path = DEFAULT_LOCK) -> None:
    """Reject startup unless every repository-locked package matches exactly."""
    drift = runtime_drift(lock_path)
    if not drift:
        return
    preview_limit = 8
    preview = "; ".join(drift[:preview_limit])
    if len(drift) > preview_limit:
        preview += f"; ... +{len(drift) - preview_limit} more"
    raise PythonRuntimeLockError(
        f"active Python environment differs from {Path(lock_path)}: {preview}"
    )
