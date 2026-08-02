from services.highlights_quality import (
    _drop_overlaps_and_repeats,
    _map_probe_segments_to_source,
    build_delivery_subtitles,
    refine_fragment_from_transcript,
    scale_subtitle_segments,
)


def _segment(start, end, text, words=None):
    return {
        "start": start,
        "end": end,
        "text": text,
        "words": words or [],
    }


def test_refine_extends_to_complete_sentence_and_maps_subtitles() -> None:
    fragment = {"start_seconds": 11.0, "end_seconds": 14.0, "hook": "x"}
    segments = [
        _segment(9.8, 12.0, "Мы должны проснуться,"),
        _segment(12.1, 15.6, "потому что время действовать уже пришло."),
    ]

    refined, evidence = refine_fragment_from_transcript(
        fragment,
        segments,
        window_start=8.0,
        window_end=18.0,
    )

    assert refined is not None
    assert refined["start_seconds"] < 10.0
    assert refined["end_seconds"] > 15.6
    assert refined["transcript"].endswith("пришло.")
    assert evidence["reason"] == "accepted"
    assert len(refined["_subtitle_source_segments"]) == 2


def test_refine_rejects_unfinished_quote() -> None:
    fragment = {"start_seconds": 10.0, "end_seconds": 14.0}
    segments = [
        _segment(9.5, 14.5, "Он пишет: «Бодрствуйте и стойте в"),
    ]

    refined, evidence = refine_fragment_from_transcript(
        fragment,
        segments,
        window_start=8.0,
        window_end=16.0,
    )

    assert refined is None
    assert evidence["reason"] in {
        "unresolved_left_context",
        "unfinished_ending",
        "unbalanced_quote",
    }


def test_refine_rejects_long_internal_silence(monkeypatch) -> None:
    monkeypatch.setenv("HIGHLIGHTS_MAX_INTERNAL_SILENCE_SECONDS", "2.5")
    fragment = {"start_seconds": 10.0, "end_seconds": 20.0}
    segments = [
        _segment(
            9.8,
            12.0,
            "Проснитесь.",
            words=[{"start": 9.8, "end": 10.8, "word": "Проснитесь."}],
        ),
        _segment(
            18.0,
            20.2,
            "Время действовать.",
            words=[
                {"start": 18.0, "end": 18.5, "word": "Время"},
                {"start": 18.6, "end": 19.7, "word": "действовать."},
            ],
        ),
    ]

    refined, evidence = refine_fragment_from_transcript(
        fragment,
        segments,
        window_start=8.0,
        window_end=22.0,
    )

    assert refined is None
    assert evidence["reason"] == "internal_silence"
    assert evidence["max_silence"] > 2.5


def test_refine_includes_adjacent_left_context_for_pronoun_start() -> None:
    fragment = {"start_seconds": 12.0, "end_seconds": 15.0}
    segments = [
        _segment(9.5, 11.5, "Перед ним стоял молодой человек."),
        _segment(11.7, 15.2, "Он понимал, что прежняя жизнь закончилась."),
    ]

    refined, _ = refine_fragment_from_transcript(
        fragment,
        segments,
        window_start=8.0,
        window_end=17.0,
    )

    assert refined is not None
    assert refined["transcript"].startswith("Перед ним стоял")


def test_overlap_and_repeated_meaning_are_removed() -> None:
    fragments = [
        {
            "start_seconds": 10.0,
            "end_seconds": 16.0,
            "transcript": "Нужно бодрствовать и твёрдо стоять в вере.",
        },
        {
            "start_seconds": 15.0,
            "end_seconds": 20.0,
            "transcript": "Другой фрагмент.",
        },
        {
            "start_seconds": 30.0,
            "end_seconds": 36.0,
            "transcript": "Нужно твёрдо стоять в вере и бодрствовать.",
        },
    ]

    accepted, rejected = _drop_overlaps_and_repeats(fragments)

    assert len(accepted) == 1
    assert {item["reason"] for item in rejected} == {
        "source_overlap",
        "repeated_meaning",
    }


def test_delivery_subtitles_are_mapped_across_fragments_and_scaled() -> None:
    fragments = [
        {
            "start_seconds": 10.0,
            "end_seconds": 14.0,
            "_subtitle_source_segments": [
                {"start": 10.5, "end": 12.0, "text": "Первый.", "words": []}
            ],
        },
        {
            "start_seconds": 30.0,
            "end_seconds": 35.0,
            "_subtitle_source_segments": [
                {"start": 31.0, "end": 33.0, "text": "Второй.", "words": []}
            ],
        },
    ]

    mapped = build_delivery_subtitles(fragments)
    assert mapped[0]["start"] == 0.5
    assert mapped[1]["start"] == 5.0

    scaled = scale_subtitle_segments(mapped, 2.0)
    assert scaled[0]["start"] == 0.25
    assert scaled[1]["start"] == 2.5



def test_probe_mapping_drops_no_word_segment_crossing_window_edge() -> None:
    windows = [
        {
            "index": 0,
            "probe_start": 0.0,
            "probe_end": 10.0,
            "source_start": 100.0,
            "source_end": 110.0,
        }
    ]
    mapped = _map_probe_segments_to_source(
        [{"start": -0.6, "end": 1.0, "text": "Чужой контекст.", "words": []}],
        windows,
    )
    assert mapped[0] == []


def test_probe_mapping_clips_word_evidence_to_exact_window() -> None:
    windows = [
        {
            "index": 0,
            "probe_start": 0.0,
            "probe_end": 10.0,
            "source_start": 100.0,
            "source_end": 110.0,
        }
    ]
    mapped = _map_probe_segments_to_source(
        [
            {
                "start": -0.3,
                "end": 1.0,
                "text": "Лишнее Проснитесь.",
                "words": [
                    {"start": -0.2, "end": -0.05, "word": "Лишнее"},
                    {"start": 0.1, "end": 0.8, "word": "Проснитесь."},
                ],
            }
        ],
        windows,
    )
    assert len(mapped[0]) == 1
    assert mapped[0][0]["text"] == "Проснитесь."
    assert mapped[0][0]["start"] == 100.0
    assert mapped[0][0]["words"][0]["start"] == 100.1
