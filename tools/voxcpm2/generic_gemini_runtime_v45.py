#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gemini MAX entrypoint with Professional Audio v4.7 QA."""
from __future__ import annotations

import os

from tools.voxcpm2 import generic_gemini_runtime as base
from tools.voxcpm2 import professional_audio_qa_v45
from tools.voxcpm2 import professional_audio_v45
from tools.voxcpm2 import professional_segmentation_v45
from tools.voxcpm2 import semantic_tts_guard_v47


def _ensure_focused_rounds() -> None:
    try:
        configured = int(os.getenv("DUB_TTS_QA_MAX_ROUNDS", "5") or "5")
    except ValueError:
        configured = 5
    os.environ["DUB_TTS_QA_MAX_ROUNDS"] = str(max(5, min(7, configured)))


def main() -> None:
    _ensure_focused_rounds()
    professional_segmentation_v45.install()
    professional_audio_v45.install()
    professional_audio_qa_v45.install()
    semantic_tts_guard_v47.install()
    base.main()


if __name__ == "__main__":
    main()
