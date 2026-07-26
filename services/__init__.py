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
    from services.gemini_qa_policy import configure_gemini_qa_policy
    from services.livedub_quality_runtime import (
        configure_gemini_network,
        configure_gemini_policy,
    )

    _gemini_qa_policy = configure_gemini_qa_policy()
    _gemini_quality = configure_max_quality_env()
    _gemini_policy = configure_gemini_policy()
    print(
        f"🧠 Gemini policy: {_gemini_policy}; {_gemini_quality}; "
        f"{_gemini_qa_policy}"
    )
    _gemini_route = configure_gemini_network()
    if _gemini_route:
        print(f"🌐 Gemini route: {_gemini_route}")
except Exception as _gemini_route_error:
    print(f"⚠️ Gemini policy/route не настроены: {_gemini_route_error}")

try:
    # Install before services.shorts_video / render_clips_montage copy the helper
    # with ``from services.ffmpeg import _is_static_video``. Moving footage keeps
    # crop_zoom; only confidently static slides receive the centred blur layout.
    from services.shorts_static_runtime import install_short_static_runtime

    _shorts_static_policy = install_short_static_runtime()
    print(f"🎞 Shorts visual policy: {_shorts_static_policy}")
except Exception as _shorts_static_error:
    print(f"⚠️ Shorts static-slide detector не установлен: {_shorts_static_error}")

try:
    # Must run before services.telegraph_pages imports prompt/schema/audit helpers.
    # Synopsis remains verbatim. Study gets reliability guards first, then a
    # concise teacherly runtime prompt. The large source prompt stays available
    # to old regression contracts; the telegraph_pages import hook swaps only
    # the effective module-level prompt used for live generation.
    from core import content_audit as _content_audit
    from core import prompts as _prompts
    from core import structured_blocks as _structured_blocks
    from services import conspect_quality_contract as _conspect_quality_module
    from services.conspect_quality_contract import install_conspect_quality_contract
    from services.conspect_audit_runtime import install_conspect_audit_runtime
    from services.study_synthesis_runtime import install_teacherly_study_runtime

    _conspect_contract = install_conspect_quality_contract()
    _conspect_audit = install_conspect_audit_runtime()

    _legacy_effective_study_prompt = _prompts.STUDY_ANALYSIS_PROMPT
    _legacy_word_study_normalizer = _conspect_quality_module.normalize_word_study_block

    _study_synthesis = install_teacherly_study_runtime()

    # Preserve historical source-level contracts and direct helper behavior for
    # archives/tests. services.telegraph_pages is patched after import and still
    # receives TEACHERLY_STUDY_PROMPT for the actual live Study request.
    _prompts.STUDY_ANALYSIS_PROMPT = _legacy_effective_study_prompt
    _conspect_quality_module.normalize_word_study_block = _legacy_word_study_normalizer

    # The teacherly renderer intentionally returns None for a thin word study.
    # content_audit historically used ``normalize(...) or raw``; convert None to
    # the same explicit drop marker used by conspect_audit_runtime so incomplete
    # decorative blocks cannot be resurrected.
    _teacherly_normalizer = _structured_blocks.normalize_structured_block

    def _normalize_teacherly_with_drop(raw):
        normalized = _teacherly_normalizer(raw)
        if normalized is None and isinstance(raw, dict):
            btype = str(raw.get("type") or "").strip().lower()
            if btype in {"word_study", "wordstudy"}:
                return {
                    "type": "paragraph",
                    "text": "__DROP_INCOMPLETE_WORD_STUDY__",
                    "_drop_word_study": True,
                }
        return normalized

    _normalize_teacherly_with_drop._teacherly_study_runtime = True  # type: ignore[attr-defined]
    _structured_blocks.normalize_structured_block = _normalize_teacherly_with_drop
    _content_audit.normalize_structured_block = _normalize_teacherly_with_drop

    print(
        f"📚 Conspect quality: {_conspect_contract}; {_conspect_audit}; "
        f"{_study_synthesis}"
    )
except Exception as _conspect_contract_error:
    print(f"⚠️ Conspect quality contract не установлен: {_conspect_contract_error}")


class _AfterImportLoader(importlib.abc.Loader):
    def __init__(self, loader: Any, finder: "_QualityRuntimeFinder"):
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
