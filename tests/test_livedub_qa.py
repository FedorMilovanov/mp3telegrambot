"""Collect the retained LiveDub QA suite with current production contracts.

The historical suite is kept in ``livedub_qa_cases.py`` so its large body stays
byte-for-byte stable. This collector replaces only obsolete assertions whose
production contract intentionally changed: the old four-mode registry and the
old user-visible 3.5 fallback chain.
"""
from __future__ import annotations

from pathlib import Path
import runpy

from handlers.mode_command import MODE_DESCRIPTIONS, MODE_LABELS, VALID_MODES

_REPLACED_CASES = {
    "test_three_modes_defined",
    "test_livedub_light_model_default_fallbacks_are_alive_models",
}
_CASES = runpy.run_path(str(Path(__file__).with_name("livedub_qa_cases.py")))
globals().update(
    {
        name: value
        for name, value in _CASES.items()
        if name.startswith("test_") and name not in _REPLACED_CASES
    }
)


def test_all_modes_defined() -> None:
    assert VALID_MODES == (
        "rus",
        "eng",
        "eng_fast",
        "eng_fast_qa",
        "shorts_max",
    )
    for mode in VALID_MODES:
        assert mode in MODE_LABELS
        assert mode in MODE_DESCRIPTIONS


def test_livedub_info_semantic_route_has_no_35_model_fallbacks(monkeypatch) -> None:
    from services.livedub_info import get_light_model, get_light_model_fallbacks

    monkeypatch.setenv("LIVEDUB_INFO_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("LIVEDUB_INFO_FALLBACK_MODELS", "gemini-3.5-flash")
    monkeypatch.setenv("GEMINI_LIGHT_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("GEMINI_LIGHT_FALLBACK_MODELS", "gemini-3.5-flash")

    assert get_light_model() == "gemini-3.6-flash"
    assert get_light_model_fallbacks() == []
