"""Resolve external executables before handing commands to subprocess."""

from __future__ import annotations

import shutil as shutil


def resolve_required_executable(name: str) -> str:
    """Return the resolved executable path or raise the usual missing-file error."""
    resolved = shutil.which(name)
    if resolved is None:
        raise FileNotFoundError(f"Required executable not found on PATH: {name}")
    return resolved
