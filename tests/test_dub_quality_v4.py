from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np

from tools.voxcpm2.dub_quality_v4 import group_cues_v4, group_ready_srt_v4
from tools.voxcpm2.voxcpm2_quality_v4_renderer import trim_candidate_edges


def _cue(start: float, end: float, text: str):
    return SimpleNamespace(start=start, end=end, text=text)


def test_gemini_groups_are_short_local_timing_anchors() -> None:
    cues = [
        _cue(0.0, 1.8, "First thought."),
        _cue(1.9, 3.8, "Second thought."),
        _cue(3.9, 5.9, "Third thought."),
        _cue(6.0, 8.0, "Fourth thought."),
    ]
    groups = group_cues_v4(cues)
    assert len(groups) >= 2
    assert all(float(item["end"]) - float(item["start"]) <= 7.05 for item in groups)


def test_ready_srt_keeps_normal_local_anchors() -> None:
    groups = group_ready_srt_v4(
        [
            _cue(0.0, 2.8, "Первая фраза."),
            _cue(3.1, 5.9, "Вторая фраза."),
            _cue(6.2, 9.0, "Третья фраза."),
        ]
    )
    assert [(item["start"], item["end"]) for item in groups] == [
        (0.0, 2.8),
        (3.1, 5.9),
        (6.2, 9.0),
    ]


def test_edge_trim_skips_isolated_chirp_and_stabilizes_preroll() -> None:
    sample_rate = 16000
    audio = np.zeros(sample_rate * 2, dtype=np.float32)
    audio[240:244] = 0.75
    start = int(sample_rate * 0.55)
    time = np.arange(int(sample_rate * 0.70), dtype=np.float32) / sample_rate
    audio[start : start + len(time)] = 0.12 * np.sin(2 * np.pi * 180 * time)
    trimmed, report = trim_candidate_edges(audio, sample_rate)
    assert report["trimmed_leading"] > 0.40
    assert 0.65 < len(trimmed) / sample_rate < 1.05


def test_quality_v4_entrypoints_disable_legacy_prompt_guard() -> None:
    gemini = Path("tools/voxcpm2/generic_gemini_runtime.py").read_text(encoding="utf-8")
    direct = Path("tools/voxcpm2/generic_direct_checked_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "legacy_semantic_guard.install = _disable_legacy_guard_install" in gemini
    assert "legacy_semantic_guard.install = _disable_legacy_guard_install" in direct
    assert "semantic_tts_guard_v4.install()" in gemini
    assert "semantic_tts_guard_v4.install()" in direct


def test_quality_v4_has_no_nested_prompt_retry_or_whole_mix_loudnorm() -> None:
    guard = Path("tools/voxcpm2/semantic_tts_guard_v4.py").read_text(encoding="utf-8")
    renderer = Path("tools/voxcpm2/voxcpm2_quality_v4_renderer.py").read_text(
        encoding="utf-8"
    )
    master = Path("tools/voxcpm2/master_quality_v4.py").read_text(encoding="utf-8")
    assert 'env["VOXCPM_PROMPT_TEXTS_JSON"]' not in guard
    assert 'env.pop("VOXCPM_PROMPT_TEXTS_JSON", None)' in guard
    assert 'values["retry_badcase"]' not in renderer
    assert 'whole_mix_loudnorm": False' in master
    assert "alimiter=limit=0.985:level=false" in master
