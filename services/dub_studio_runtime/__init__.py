#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write-through Dub supervisor facade with command-surface hardening.

The established implementation remains in ``services/dub_studio_runtime.py``.
This package reads the shared worker release identity, preserves write-through
monkeypatch behavior and registers reliable multiline Dub commands plus stale
callback-card recovery.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
from typing import Any

from services.dub_worker_release import WORKER_RUNTIME

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "dub_studio_runtime.py"
_SPEC = importlib.util.spec_from_file_location(
    "services._dub_studio_runtime_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить Dub Studio supervisor: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _legacy
_SPEC.loader.exec_module(_legacy)

_WORKER_RUNTIME = WORKER_RUNTIME
_legacy._WORKER_RUNTIME = _WORKER_RUNTIME

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))
globals()["_WORKER_RUNTIME"] = _WORKER_RUNTIME

_legacy_install_dub_studio_runtime = _legacy.install_dub_studio_runtime


def _install_multicommand_build_wrapper() -> None:
    from telegram.ext import ApplicationBuilder

    current = ApplicationBuilder.build
    if getattr(current, "_dub_multicommand_build_wrapper", False):
        return

    def build_with_multicommand(self: Any) -> Any:
        application = current(self)
        from handlers.dub_multicommand import register_dub_multicommand_handler

        register_dub_multicommand_handler(application)
        return application

    build_with_multicommand._dub_multicommand_build_wrapper = True  # type: ignore[attr-defined]
    ApplicationBuilder.build = build_with_multicommand


def install_dub_studio_runtime() -> None:
    """Install the proven runtime, command surface and current release-health hook."""
    _legacy._WORKER_RUNTIME = WORKER_RUNTIME
    globals()["_WORKER_RUNTIME"] = WORKER_RUNTIME
    _legacy_install_dub_studio_runtime()
    # Legacy installation imports health; reassert the shared release after all
    # import-order side effects have completed.
    _legacy._WORKER_RUNTIME = WORKER_RUNTIME
    globals()["_WORKER_RUNTIME"] = WORKER_RUNTIME
    _install_multicommand_build_wrapper()
    from services.dub_release_health_v64 import install_release_health_hook

    install_release_health_hook()


class _WriteThroughModule(types.ModuleType):
    """Keep package assignments and legacy function globals synchronized."""

    def __setattr__(self, name: str, value: Any) -> None:
        types.ModuleType.__setattr__(self, name, value)
        if name in {"_legacy", "__class__"} or name.startswith("__"):
            return
        legacy = types.ModuleType.__getattribute__(self, "_legacy")
        if hasattr(legacy, name):
            setattr(legacy, name, value)

    def __getattr__(self, name: str) -> Any:
        legacy = types.ModuleType.__getattribute__(self, "_legacy")
        return getattr(legacy, name)


_module = sys.modules[__name__]
_module.__class__ = _WriteThroughModule

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {
        "WORKER_RUNTIME",
        "_WORKER_RUNTIME",
        "_install_multicommand_build_wrapper",
        "install_dub_studio_runtime",
    }
)
