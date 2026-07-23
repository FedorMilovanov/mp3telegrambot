"""External services — Telegraph, Gemini, FFmpeg, search, PDF.

The package is imported by ``bot_new.py`` only after ``load_dotenv()``.  That
makes it the earliest reliable place to select the project-wide Gemini model,
maximum-thinking policy and route before ``core.globals`` creates google-genai
clients.  A one-shot import hook installs the remaining runtime adapters after
LiveDub companion modules have loaded; no entrypoint rewrite is required.
"""
from __future__ import annotations

import importlib.abc
import importlib.util
import sys
from types import ModuleType
from typing import Any

try:
    from services.gemini_max_quality import configure_max_quality_env
    from services.livedub_quality_runtime import (
        configure_gemini_network,
        configure_gemini_policy,
    )

    _gemini_quality = configure_max_quality_env()
    _gemini_policy = configure_gemini_policy()
    print(f"🧠 Gemini policy: {_gemini_policy}; {_gemini_quality}")
    _gemini_route = configure_gemini_network()
    if _gemini_route:
        print(f"🌐 Gemini route: {_gemini_route}")
except Exception as _gemini_route_error:
    print(f"⚠️ Gemini policy/route не настроены: {_gemini_route_error}")

try:
    # Must run before services.telegraph_pages imports prompt/schema/audit helpers.
    # Synopsis remains verbatim.  Study gets reliability guards first, then the
    # final concise teacherly prompt/public-prose contract.
    from services.conspect_quality_contract import install_conspect_quality_contract
    from services.conspect_audit_runtime import install_conspect_audit_runtime
    from services.study_synthesis_runtime import install_teacherly_study_runtime

    _conspect_contract = install_conspect_quality_contract()
    _conspect_audit = install_conspect_audit_runtime()
    _study_synthesis = install_teacherly_study_runtime()
    print(
        f"📚 Conspect quality: {_conspect_contract}; {_conspect_audit}; "
        f"{_study_synthesis}"
    )
except Exception as _conspect_contract_error:
    print(f"⚠️ Conspect quality contract не установлен: {_conspect_contract_error}")


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
            from services.gemini_max_quality import install_max_quality_runtime
            from services.livedub_quality_runtime import install_livedub_quality_runtime

            install_max_quality_runtime()
            install_livedub_quality_runtime()
        except Exception as exc:
            print(f"⚠️ Gemini/LiveDub quality runtime не установлен: {exc}")


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
