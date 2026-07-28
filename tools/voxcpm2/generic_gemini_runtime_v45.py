#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gemini MAX entrypoint with Professional Audio v4.6 QA."""
from __future__ import annotations

from tools.voxcpm2 import generic_gemini_runtime as base
from tools.voxcpm2 import professional_audio_qa_v45
from tools.voxcpm2 import professional_audio_v45
from tools.voxcpm2 import professional_segmentation_v45
from tools.voxcpm2 import semantic_tts_guard_v46


def main() -> None:
    professional_segmentation_v45.install()
    professional_audio_v45.install()
    professional_audio_qa_v45.install()
    semantic_tts_guard_v46.install()
    base.main()


if __name__ == "__main__":
    main()
