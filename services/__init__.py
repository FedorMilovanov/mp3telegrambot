"""External services — Telegraph, Gemini, FFmpeg, search, PDF.

The package is imported by ``bot_new.py`` only after ``load_dotenv()``.  That
makes it the earliest reliable place to force Gemini through the configured
v2rayN route, before ``core.globals`` creates google-genai clients.  A tiny
one-shot import hook installs the remaining LiveDub runtime after its companion
modules have loaded; no entrypoint rewrite is required.
"""
from __future__ import annotations

import importlib.abc
import importlib.util
import sys
from types import ModuleType
from typing import Any

try:
    from services.livedub_quality_runtime import configure_gemini_network

    _gemini_route = configure_gemini_network()
    if _gemini_route:
        print(f"🌐 Gemini explicit proxy: {_gemini_route}")
except Exception as _gemini_route_error:
    print(f"⚠️ Явный маршрут Gemini не настроен: {_gemini_route_error}")


class _AfterImportLoader(importlib.abc.Loader):
    def __init__(self, loader: Any, finder: "_QualityRuntimeFinder") -> None:
        self._loader = loader
        self._finder = finder

    def create_module(self, spec):
        create = getattr(self._loader, "create_module", None)
        return create(spec) if create else None

    def exec_module(self, module: ModuleType) -> None:
        self._loader.exec_module(module)
        try:
            sys.meta_path.remove(self._finder)
        except ValueError:
            pass
        try:
            from services.livedub_quality_runtime import install_livedub_quality_runtime

            install_livedub_quality_runtime()
        except Exception as exc:
            print(f"⚠️ LiveDub quality runtime не установлен: {exc}")


class _QualityRuntimeFinder(importlib.abc.MetaPathFinder):
    target = "services.livedub_audio_dedupe"

    def find_spec(self, fullname, path=None, target=None):
        if fullname != self.target:
            return None
        try:
            sys.meta_path.remove(self)
        except ValueError:
            pass
        try:
            spec = importlib.util.find_spec(fullname)
        finally:
            sys.meta_path.insert(0, self)
        if spec is not None and spec.loader is not None:
            spec.loader = _AfterImportLoader(spec.loader, self)
        return spec


if not any("pytest" in str(arg).lower() for arg in sys.argv):
    sys.meta_path.insert(0, _QualityRuntimeFinder())
