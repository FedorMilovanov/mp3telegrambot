"""Collect the retained LiveDub QA suite with the current mode registry contract.

The historical suite is kept in ``livedub_qa_cases.py`` so its large body stays
byte-for-byte stable. This collector replaces only the obsolete four-mode
assertion after SHORTS FACTORY MAX became a production mode.
"""
from __future__ import annotations

from pathlib import Path
import runpy

from handlers.mode_command import MODE_DESCRIPTIONS, MODE_LABELS, VALID_MODES

_CASES = runpy.run_path(str(Path(__file__).with_name("livedub_qa_cases.py")))
globals().update(
    {
        name: value
        for name, value in _CASES.items()
        if name.startswith("test_") and name != "test_three_modes_defined"
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
