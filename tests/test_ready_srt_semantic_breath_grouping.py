from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.voxcpm2 import dub_quality_v4


def cue(start: float, end: float, text: str) -> SimpleNamespace:
    return SimpleNamespace(start=start, end=end, text=text)


def test_short_heading_and_following_thought_become_one_breath() -> None:
    groups = dub_quality_v4.group_ready_srt_v4(
        [
            cue(0.00, 1.10, "Двадцать пятый стих:"),
            cue(1.15, 2.75, "Она облечена силой и достоинством"),
            cue(2.80, 4.35, "и смеётся над грядущим."),
        ],
        max_seconds=5.4,
    )

    assert len(groups) == 1
    assert groups[0]["grouping_policy"] == "ready-srt-semantic-breath-grouping-v1"
    assert groups[0]["source_cue_count"] == 3
    assert groups[0]["source"] == (
        "Двадцать пятый стих: Она облечена силой и достоинством "
        "и смеётся над грядущим."
    )


def test_large_source_gap_is_a_hard_breath_boundary() -> None:
    groups = dub_quality_v4.group_ready_srt_v4(
        [
            cue(0.00, 1.80, "Первая мысль продолжается."),
            cue(2.30, 4.10, "После большой паузы начинается другая."),
        ],
        max_seconds=5.4,
    )

    assert len(groups) == 2
    assert groups[0]["source"] == "Первая мысль продолжается."
    assert groups[1]["source"] == "После большой паузы начинается другая."


def test_final_gryadyot_stays_at_end_of_its_synthesis_breath() -> None:
    groups = dub_quality_v4.group_ready_srt_v4(
        [
            cue(0.00, 2.20, "Она надеется на то, что грядёт."),
            cue(2.25, 3.70, "И смеётся без страха."),
        ],
        max_seconds=5.4,
    )

    assert len(groups) == 2
    assert groups[0]["source"].endswith("грядёт.")
    assert groups[1]["source"].startswith("И смеётся")


def test_grouping_preserves_every_word_and_outer_anchors() -> None:
    source = [
        cue(0.20, 1.55, "Раз, два,"),
        cue(1.60, 3.00, "три и четыре."),
        cue(3.10, 5.05, "Следующее предложение здесь."),
    ]

    groups = dub_quality_v4.group_ready_srt_v4(source, max_seconds=5.4)

    assert groups[0]["start"] == 0.20
    assert groups[-1]["end"] == 5.05
    assert " ".join(item["source"] for item in groups) == (
        "Раз, два, три и четыре. Следующее предложение здесь."
    )


def test_physically_overloaded_single_cue_fails_closed() -> None:
    overloaded = " ".join(f"слово{index}" for index in range(20))

    with pytest.raises(RuntimeError, match="физически допустимые semantic breaths"):
        dub_quality_v4.group_ready_srt_v4(
            [cue(0.00, 1.20, overloaded)],
            max_seconds=5.4,
        )
