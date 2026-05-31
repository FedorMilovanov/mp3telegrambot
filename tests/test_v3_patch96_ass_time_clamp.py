#!/usr/bin/env python3
"""Regression: _seconds_to_ass_time must never emit malformed timestamps.

Whisper occasionally returns a slightly negative word.start. The old formatter
produced '-1:59:59.-50' for negative input, which libass/ffmpeg reject and which
broke rendering of the whole subtitle line. Negative input must clamp to zero.
"""
import re

from services.shorts_video import _seconds_to_ass_time

_ASS_TIME_RE = re.compile(r"^\d:[0-5]\d:[0-5]\d\.\d\d$")


def test_negative_clamped_to_zero():
    assert _seconds_to_ass_time(-0.5) == "0:00:00.00"
    assert _seconds_to_ass_time(-2.3) == "0:00:00.00"


def test_positive_values_unchanged():
    assert _seconds_to_ass_time(0) == "0:00:00.00"
    assert _seconds_to_ass_time(1.234) == "0:00:01.23"
    assert _seconds_to_ass_time(65.5) == "0:01:05.50"
    assert _seconds_to_ass_time(3661.99) == "1:01:01.99"


def test_output_always_well_formed():
    for v in (-100, -0.01, 0, 0.999, 1.0, 59.99, 3600.5, 7322.42):
        out = _seconds_to_ass_time(v)
        assert _ASS_TIME_RE.match(out), f"malformed ASS time for {v!r}: {out!r}"
