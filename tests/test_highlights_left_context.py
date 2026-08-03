from services.highlights_quality import refine_fragment_from_transcript


def _segment(start: float, end: float, text: str) -> dict:
    return {
        "start": start,
        "end": end,
        "text": text,
        "words": [],
    }


def test_refine_recovers_washer_mid_thought_start() -> None:
    fragment = {"start_seconds": 12.0, "end_seconds": 15.5}
    segments = [
        _segment(9.2, 11.4, "Христианская жизнь проходит в напряжении."),
        _segment(11.55, 16.2, "В смысле, вы живёте между двумя мирами."),
    ]

    refined, evidence = refine_fragment_from_transcript(
        fragment,
        segments,
        window_start=8.0,
        window_end=18.0,
    )

    assert refined is not None
    assert refined["transcript"].startswith("Христианская жизнь")
    assert evidence["reason"] == "accepted"


def test_refine_rejects_washer_mid_thought_start_without_context() -> None:
    fragment = {"start_seconds": 12.0, "end_seconds": 15.5}
    segments = [
        _segment(
            11.5,
            16.2,
            "В смысле, вы живёте между двумя мирами и должны бодрствовать.",
        ),
    ]

    refined, evidence = refine_fragment_from_transcript(
        fragment,
        segments,
        window_start=11.5,
        window_end=18.0,
    )

    assert refined is None
    assert evidence["reason"] == "unresolved_left_context"


def test_refine_includes_unfinished_previous_segment_without_keyword() -> None:
    fragment = {"start_seconds": 12.0, "end_seconds": 16.0}
    segments = [
        _segment(9.4, 11.5, "Христианин должен помнить,"),
        _segment(11.65, 16.5, "земной мир не является его окончательным домом."),
    ]

    refined, evidence = refine_fragment_from_transcript(
        fragment,
        segments,
        window_start=8.0,
        window_end=18.0,
    )

    assert refined is not None
    assert refined["transcript"].startswith("Христианин должен")
    assert evidence["reason"] == "accepted"
