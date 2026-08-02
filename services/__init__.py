"""External services — Telegraph, Gemini, FFmpeg, search, PDF.

The package is imported by ``bot_new.py`` only after ``load_dotenv()``. That
makes it the earliest reliable place to select project-wide policies before
``core.globals`` creates external clients. Runtime hooks still install during
package import for compatibility, but import never writes to stdout/stderr.
Diagnostics and transitive import output are recorded structurally and emitted
explicitly by the entrypoint.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import importlib.abc
import importlib.util
from io import StringIO
import sys
import threading
from types import ModuleType
from typing import Any

SERVICE_BOOTSTRAP_DIAGNOSTICS_POLICY = "recorded-explicit-service-bootstrap-v1"
_CAPTURED_OUTPUT_LIMIT = 8000
_BOOTSTRAP_EVENT_LOCK = threading.RLock()
_BOOTSTRAP_EVENTS: list[dict[str, Any]] = []


def _record_bootstrap(component: str, *, ok: bool, detail: object) -> None:
    component_name = str(component or "").strip()
    detail_text = str(detail or "").strip()
    if not component_name:
        component_name = "unknown"
    if not detail_text:
        detail_text = "ok" if ok else "unknown error"
    with _BOOTSTRAP_EVENT_LOCK:
        _BOOTSTRAP_EVENTS.append(
            {
                "sequence": len(_BOOTSTRAP_EVENTS) + 1,
                "component": component_name,
                "ok": bool(ok),
                "detail": detail_text,
                "policy": SERVICE_BOOTSTRAP_DIAGNOSTICS_POLICY,
            }
        )


def _runtime_install_detail(result: object, *, installed_label: str = "installed") -> str:
    """Render installer results without leaking Python's meaningless ``None``.

    Runtime installers commonly signal success by returning normally rather than
    by returning a status string.  A successful ``None`` result is therefore an
    installation state, not diagnostic payload.
    """
    if result is None:
        return installed_label
    text = str(result).strip()
    return text or installed_label


def _compact_captured_output(value: str) -> str:
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    compact = " | ".join(lines)
    if len(compact) > _CAPTURED_OUTPUT_LIMIT:
        compact = "…" + compact[-_CAPTURED_OUTPUT_LIMIT:]
    return compact


@contextmanager
def _capture_bootstrap_output(component: str) -> Iterator[None]:
    """Capture import/installer output without hiding it from diagnostics."""
    stdout_buffer = StringIO()
    stderr_buffer = StringIO()
    try:
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            yield
    finally:
        stdout_value = _compact_captured_output(stdout_buffer.getvalue())
        stderr_value = _compact_captured_output(stderr_buffer.getvalue())
        if stdout_value:
            _record_bootstrap(
                f"{component} captured stdout",
                ok=True,
                detail=stdout_value,
            )
        if stderr_value:
            _record_bootstrap(
                f"{component} captured stderr",
                ok=True,
                detail=stderr_value,
            )


def service_bootstrap_events() -> tuple[dict[str, Any], ...]:
    """Return an immutable snapshot without emitting import-time output."""
    with _BOOTSTRAP_EVENT_LOCK:
        return tuple(dict(event) for event in _BOOTSTRAP_EVENTS)


def emit_service_bootstrap_diagnostics(
    sink: Callable[[str], Any] | None = None,
) -> int:
    """Emit the recorded bootstrap report only when an entrypoint asks for it."""
    target = sink or print
    events = service_bootstrap_events()
    for event in events:
        marker = "✅" if event["ok"] else "⚠️"
        target(
            f"{marker} {event['component']}: {event['detail']} "
            f"[{event['policy']}]"
        )
    return len(events)


try:
    with _capture_bootstrap_output("Polling reliability runtime"):
        from services.polling_reliability_runtime import (
            install_polling_reliability_runtime,
        )

        _polling_reliability = install_polling_reliability_runtime()
    _record_bootstrap(
        "Polling reliability runtime",
        ok=True,
        detail=_polling_reliability or "installed",
    )
except Exception as _polling_reliability_error:
    _record_bootstrap(
        "Polling reliability runtime",
        ok=False,
        detail=_polling_reliability_error,
    )

try:
    with _capture_bootstrap_output("Gemini policy/route"):
        from services.gemini_max_quality import configure_max_quality_env
        from services.gemini_qa_policy import configure_gemini_qa_policy
        from services.livedub_quality_runtime import (
            configure_gemini_network,
            configure_gemini_policy,
        )

        _gemini_qa_policy = configure_gemini_qa_policy()
        _gemini_quality = configure_max_quality_env()
        _gemini_policy = configure_gemini_policy()
        _gemini_route = configure_gemini_network()
    _record_bootstrap(
        "Gemini policy",
        ok=True,
        detail=f"{_gemini_policy}; {_gemini_quality}; {_gemini_qa_policy}",
    )
    if _gemini_route:
        _record_bootstrap("Gemini route", ok=True, detail=_gemini_route)
except Exception as _gemini_route_error:
    _record_bootstrap(
        "Gemini policy/route",
        ok=False,
        detail=_gemini_route_error,
    )

try:
    # Install before services.shorts_video / render_clips_montage copy the helper
    # with ``from services.ffmpeg import _is_static_video``. Moving footage keeps
    # crop_zoom; only confidently static slides receive the centred blur layout.
    with _capture_bootstrap_output("Shorts visual policy"):
        from services.shorts_static_runtime import install_short_static_runtime

        _shorts_static_policy = install_short_static_runtime()
    _record_bootstrap(
        "Shorts visual policy",
        ok=True,
        detail=_shorts_static_policy,
    )
except Exception as _shorts_static_error:
    _record_bootstrap(
        "Shorts static-slide detector",
        ok=False,
        detail=_shorts_static_error,
    )

try:
    # Must run before services.telegraph_pages imports prompt/schema/audit helpers.
    # Synopsis remains verbatim. Study gets reliability guards first, then a
    # concise teacherly runtime prompt. The large source prompt stays available
    # to old regression contracts; the telegraph_pages import hook swaps only
    # the effective module-level prompt used for live generation.
    with _capture_bootstrap_output("Conspect quality"):
        from core import content_audit as _content_audit
        from core import prompts as _prompts
        from core import structured_blocks as _structured_blocks
        from services import conspect_quality_contract as _conspect_quality_module
        from services.conspect_audit_runtime import install_conspect_audit_runtime
        from services.conspect_quality_contract import install_conspect_quality_contract
        from services.study_synthesis_runtime import install_teacherly_study_runtime

        _conspect_contract = install_conspect_quality_contract()
        _conspect_audit = install_conspect_audit_runtime()

        _legacy_effective_study_prompt = _prompts.STUDY_ANALYSIS_PROMPT
        _legacy_word_study_normalizer = (
            _conspect_quality_module.normalize_word_study_block
        )

        _study_synthesis = install_teacherly_study_runtime()

        # Preserve historical source-level contracts and direct helper behavior for
        # archives/tests. services.telegraph_pages is patched after import and still
        # receives TEACHERLY_STUDY_PROMPT for the actual live Study request.
        _prompts.STUDY_ANALYSIS_PROMPT = _legacy_effective_study_prompt
        _conspect_quality_module.normalize_word_study_block = (
            _legacy_word_study_normalizer
        )

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

    _record_bootstrap(
        "Conspect quality",
        ok=True,
        detail=f"{_conspect_contract}; {_conspect_audit}; {_study_synthesis}",
    )
except Exception as _conspect_contract_error:
    _record_bootstrap(
        "Conspect quality contract",
        ok=False,
        detail=_conspect_contract_error,
    )


class _AfterImportLoader(importlib.abc.Loader):
    def __init__(self, loader: Any, finder: "_QualityRuntimeFinder"):
        self._loader = loader
        self._finder = finder

    def create_module(self, spec):
        create = getattr(self._loader, "create_module", None)
        return create(spec) if create else None

    def exec_module(self, module: ModuleType) -> None:
        with _capture_bootstrap_output("livedub_audio_dedupe import"):
            self._loader.exec_module(module)
        try:
            sys.meta_path.remove(self._finder)
        except ValueError:
            pass
        try:
            with _capture_bootstrap_output("Gemini/LiveDub quality runtime"):
                from services.gemini_max_quality import install_max_quality_runtime
                from services.livedub_quality_runtime import (
                    install_livedub_quality_runtime,
                )

                max_quality = install_max_quality_runtime()
                livedub_quality = install_livedub_quality_runtime()
            _record_bootstrap(
                "Gemini/LiveDub quality runtime",
                ok=True,
                detail=(
                    "Gemini="
                    + _runtime_install_detail(max_quality, installed_label="installed")
                    + "; LiveDub="
                    + _runtime_install_detail(livedub_quality, installed_label="installed")
                ),
            )
        except Exception as exc:
            _record_bootstrap(
                "Gemini/LiveDub quality runtime",
                ok=False,
                detail=exc,
            )


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


__all__ = [
    "SERVICE_BOOTSTRAP_DIAGNOSTICS_POLICY",
    "emit_service_bootstrap_diagnostics",
    "service_bootstrap_events",
]
