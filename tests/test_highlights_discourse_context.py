from __future__ import annotations

import pytest

from services.highlights_quality import refine_fragment_from_transcript


MARKERS = (
    "То есть",
    "Иными словами",
    "Другими словами",
    "Например",
    "Кроме того",
    "Более того",
    "Таким образом",
    "С другой стороны",
)


def _segment(start: float, end: float, text: str) -> dict:
    return {"start": start, "end": end, "text": text, "words": []}


@pytest.mark.parametrize("marker", MARKERS)
def test_discourse_marker_without_left_context_is_rejected(marker: str) -> None:
    fragment = {"start_seconds": 12.0, "end_seconds": 17.0}
    refined, evidence = refine_fragment_from_transcript(
        fragment,
        [_segment(
            11.5,
            17.5,
            f"{marker}, христианин должен бодрствовать и твёрдо стоять в вере.",
        )],
        window_start=11.5,
        window_end=18.0,
    )

    assert refined is None
    assert evidence["reason"] == "unresolved_left_context"


@pytest.mark.parametrize("marker", MARKERS)
def test_discourse_marker_recovers_completed_left_context(marker: str) -> None:
    fragment = {"start_seconds": 12.0, "end_seconds": 17.0}
    refined, evidence = refine_fragment_from_transcript(
        fragment,
        [
            _segment(8.9, 11.3, "Главная мысль уже была сформулирована."),
            _segment(
                11.5,
                17.5,
                f"{marker}, христианин должен бодрствовать и твёрдо стоять в вере.",
            ),
        ],
        window_start=8.0,
        window_end=18.0,
    )

    assert refined is not None, evidence
    assert refined["transcript"].startswith("Главная мысль")
    assert evidence["reason"] == "accepted"


def test_marker_matching_does_not_use_word_prefixes() -> None:
    fragment = {"start_seconds": 12.0, "end_seconds": 17.0}
    refined, evidence = refine_fragment_from_transcript(
        fragment,
        [_segment(
            11.5,
            17.5,
            "Напримерный образ не нужен, человек должен твёрдо стоять в вере.",
        )],
        window_start=11.5,
        window_end=18.0,
    )

    assert refined is not None, evidence
    assert evidence["reason"] == "accepted"


@pytest.mark.parametrize("prefix", ("— ", "«"))
def test_marker_is_detected_after_leading_punctuation(prefix: str) -> None:
    fragment = {"start_seconds": 12.0, "end_seconds": 17.0}
    ending = "»" if prefix == "«" else ""
    refined, evidence = refine_fragment_from_transcript(
        fragment,
        [_segment(
            11.5,
            17.5,
            f"{prefix}То есть, человек должен бодрствовать и стоять в вере.{ending}",
        )],
        window_start=11.5,
        window_end=18.0,
    )

    assert refined is None
    assert evidence["reason"] == "unresolved_left_context"
