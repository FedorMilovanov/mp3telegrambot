#!/usr/bin/env python3
from __future__ import annotations

import os
import traceback
from contextlib import contextmanager
from typing import Callable

from converters.caption import _polish_caption_sentence_punctuation
from core.person_names import normalize_person_names
from core.russian_style import polish_public_russian_text
from core.synopsis_timestamps import (
    reconcile_synopsis_timestamps,
    section_anchor_seconds,
    unresolved_timestamp_issues,
)
from core.text_utils import sentence_case_russian_title
from services.highlights_quality import (
    _drop_overlaps_and_repeats,
    _map_probe_segments_to_source,
    build_delivery_subtitles,
    refine_fragment_from_transcript,
    scale_subtitle_segments,
)
from services.media_delivery_probe import (
    MediaProbe,
    evaluate_highlights_delivery,
    parse_silencedetect,
    resolve_delivery_timing,
)
from services.shorts_video import _prepare_short_hook, build_short_caption
from services.telegraph_edit import (
    classify_telegraph_edit_error,
    telegraph_page_path,
    telegraph_retry_delay,
)

Probe = tuple[str, str, Callable[[], None]]
PROBES: list[Probe] = []


def probe(name: str, severity: str = "hard"):
    def decorator(fn: Callable[[], None]) -> Callable[[], None]:
        PROBES.append((name, severity, fn))
        return fn
    return decorator


def _caption(hook: str, *, kind: str = "") -> str:
    return build_short_caption(
        candidate={"hook": hook, "kind": kind, "hashtags": []},
        performer="",
        real_author="Пол Вошер",
        real_event="",
        format_name="sermon",
    )


def _seg(start: float, end: float, text: str, words=None) -> dict:
    return {"start": start, "end": end, "text": text, "words": words or []}


@contextmanager
def _env(**values: str):
    old = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _dependent_start_probe(prefix: str) -> None:
    fragment = {"start_seconds": 12.0, "end_seconds": 17.0}
    text = f"{prefix}, христианин должен бодрствовать и твёрдо стоять в вере."
    refined, evidence = refine_fragment_from_transcript(
        fragment,
        [_seg(11.5, 17.5, text)],
        window_start=11.5,
        window_end=18.0,
    )
    assert refined is None, (prefix, evidence, refined)
    assert evidence["reason"] == "unresolved_left_context", evidence


@probe("short_title_exact_separator")
def _():
    assert _caption(
        "Сомнение - Это не Просто Слабость, Это Прямое Оскорбление Характера Бога"
    ) == (
        "Сомнение — Это не Просто Слабость, Это Прямое Оскорбление "
        "Характера Бога - Пол Вошер"
    )


@probe("short_title_question_separator")
def _():
    assert _caption("Кто такой настоящий мужчина в браке?") == (
        "Кто Такой Настоящий Мужчина в Браке? - Пол Вошер"
    )


@probe("short_title_exclamation_separator")
def _():
    assert _caption("Бодрствуйте и стойте в вере!") == (
        "Бодрствуйте и Стойте в Вере! - Пол Вошер"
    )


@probe("short_title_word_hyphen_preserved")
def _():
    assert _prepare_short_hook(
        "Действовать по-мужски - Прямой призыв", "Пол Вошер"
    ) == "Действовать по-мужски — Прямой призыв"


@probe("short_title_duplicate_author_removed")
def _():
    assert _caption("Сомнение — Это Оскорбление Бога - Пол Вошер") == (
        "Сомнение — Это Оскорбление Бога - Пол Вошер"
    )


@probe("short_title_quote_contract")
def _():
    assert _caption("Сомнение - Это оскорбление Бога", kind="quote") == (
        "«Сомнение — Это Оскорбление Бога» - Пол Вошер"
    )


@probe("name_normalizer_preserves_em_dash")
def _():
    value = "Сомнение — Это не Слабость"
    assert normalize_person_names(value) == value


@probe("sentence_case_preserves_em_dash")
def _():
    assert sentence_case_russian_title(
        "Сомнение — Это не слабость", aggressive_title_case=True
    ) == "Сомнение — Это не Слабость"


@probe("emoji_period_moves_before_emoji")
def _():
    assert _polish_caption_sentence_punctuation(
        "готовности к испытаниям ⚔️."
    ) == "готовности к испытаниям. ⚔️"


@probe("emoji_question_moves_before_emoji")
def _():
    assert _polish_caption_sentence_punctuation("Готов ли ты 🕊️?") == "Готов ли ты? 🕊️"


@probe("emoji_exclamation_moves_before_emoji")
def _():
    assert _polish_caption_sentence_punctuation("Бодрствуйте ⚔️!") == "Бодрствуйте! ⚔️"


@probe("emoji_inside_sentence_unchanged")
def _():
    assert _polish_caption_sentence_punctuation("⚔️ Боритесь за веру.") == "⚔️ Боритесь за веру."


@probe("russian_calque_nominative")
def _():
    assert polish_public_russian_text(
        "Духовное укомплектование мужчин начинается со Слова"
    ) == "Духовная подготовка мужчин начинается со Слова"


@probe("russian_calque_genitive")
def _():
    assert polish_public_russian_text(
        "необходимость духовного укомплектования мужчин"
    ) == "необходимость духовной подготовки мужчин"


@probe("russian_calque_dative")
def _():
    assert polish_public_russian_text(
        "призыв к духовному укомплектованию мужей"
    ) == "призыв к духовной подготовке мужчин"


@probe("russian_calque_instrumental")
def _():
    assert polish_public_russian_text(
        "заниматься духовным укомплектованием мужей"
    ) == "заниматься духовной подготовкой мужчин"


@probe("russian_logistics_untouched")
def _():
    assert polish_public_russian_text("Укомплектование подразделения завершено") == (
        "Укомплектование подразделения завершено"
    )


def _good_probe(**overrides) -> MediaProbe:
    values = dict(
        duration=20.0,
        width=720,
        height=1280,
        audio_sample_rate=48000,
        audio_codec="aac",
        has_video=True,
        has_audio=True,
        size_mb=5.0,
    )
    values.update(overrides)
    return MediaProbe(**values)


@probe("media_rejects_96khz")
def _():
    report = evaluate_highlights_delivery(
        _good_probe(audio_sample_rate=96000), [], expected_duration=20.0,
        max_internal_silence=2.8,
    )
    assert "unexpected_audio_sample_rate" in report["reasons"]


@probe("media_rejects_non_aac")
def _():
    report = evaluate_highlights_delivery(
        _good_probe(audio_codec="opus"), [], expected_duration=20.0,
        max_internal_silence=2.8,
    )
    assert "unexpected_audio_codec" in report["reasons"]


@probe("media_rejects_wrong_dimensions")
def _():
    report = evaluate_highlights_delivery(
        _good_probe(width=1080, height=1920), [], expected_duration=20.0,
        max_internal_silence=2.8,
    )
    assert "unexpected_dimensions" in report["reasons"]


@probe("media_rejects_missing_audio")
def _():
    report = evaluate_highlights_delivery(
        _good_probe(has_audio=False), [], expected_duration=20.0,
        max_internal_silence=2.8,
    )
    assert "audio_stream_missing" in report["reasons"]


@probe("media_rejects_missing_video")
def _():
    report = evaluate_highlights_delivery(
        _good_probe(has_video=False), [], expected_duration=20.0,
        max_internal_silence=2.8,
    )
    assert "video_stream_missing" in report["reasons"]


@probe("media_rejects_duration_mismatch")
def _():
    report = evaluate_highlights_delivery(
        _good_probe(duration=25.0), [], expected_duration=20.0,
        max_internal_silence=2.8,
    )
    assert "duration_mismatch" in report["reasons"]


@probe("media_rejects_long_internal_silence")
def _():
    report = evaluate_highlights_delivery(
        _good_probe(), [(7.0, 10.2)], expected_duration=20.0,
        max_internal_silence=2.8,
    )
    assert "long_internal_silence" in report["reasons"]


@probe("media_allows_tiny_edge_silence")
def _():
    report = evaluate_highlights_delivery(
        _good_probe(), [(0.0, 0.3), (19.7, 20.0)], expected_duration=20.0,
        max_internal_silence=2.8,
    )
    assert report["accepted"] is True


@probe("silence_parser_merges_adjacent_intervals")
def _():
    stderr = """
    silence_start: 5.0
    silence_end: 7.0 | silence_duration: 2.0
    silence_start: 7.05
    silence_end: 9.0 | silence_duration: 1.95
    """
    assert parse_silencedetect(stderr, duration=12.0) == [(5.0, 9.0)]


@probe("silence_parser_ignores_malformed_values")
def _():
    stderr = "silence_start: nope\nsilence_end: bad"
    assert parse_silencedetect(stderr, duration=12.0) == []


@probe("timing_failed_speed_does_not_shrink")
def _():
    timing = resolve_delivery_timing(
        source_start=338.5, raw_duration=129.0, source_duration=3596.0,
        speed=1.5, speed_applied=False, final_duration=0.0,
    )
    assert timing.delivery_duration == 129.0
    assert timing.source_end == 467.5


@probe("timing_success_uses_measured_duration")
def _():
    timing = resolve_delivery_timing(
        source_start=338.5, raw_duration=129.0, source_duration=3596.0,
        speed=1.5, speed_applied=True, final_duration=86.12,
    )
    assert timing.delivery_duration == 86.12


@probe("timing_source_end_clamped")
def _():
    timing = resolve_delivery_timing(
        source_start=95.0, raw_duration=20.0, source_duration=100.0,
        speed=1.0, speed_applied=False, final_duration=0.0,
    )
    assert timing.source_end == 100.0


@probe("highlights_recovers_v_smysle_context")
def _():
    fragment = {"start_seconds": 12.0, "end_seconds": 15.5}
    refined, evidence = refine_fragment_from_transcript(
        fragment,
        [
            _seg(9.2, 11.4, "Христианская жизнь проходит в напряжении."),
            _seg(11.55, 16.2, "В смысле, вы живёте между двумя мирами."),
        ],
        window_start=8.0,
        window_end=18.0,
    )
    assert refined is not None, evidence
    assert refined["transcript"].startswith("Христианская жизнь")


@probe("highlights_rejects_v_smysle_without_context")
def _():
    fragment = {"start_seconds": 12.0, "end_seconds": 15.5}
    refined, evidence = refine_fragment_from_transcript(
        fragment,
        [_seg(11.5, 16.2, "В смысле, вы живёте между двумя мирами и бодрствуете.")],
        window_start=11.5,
        window_end=18.0,
    )
    assert refined is None
    assert evidence["reason"] == "unresolved_left_context"


@probe("highlights_recovers_pronoun_context")
def _():
    fragment = {"start_seconds": 12.0, "end_seconds": 15.0}
    refined, _ = refine_fragment_from_transcript(
        fragment,
        [
            _seg(9.5, 11.5, "Перед ним стоял молодой человек."),
            _seg(11.7, 15.2, "Он понимал, что прежняя жизнь закончилась."),
        ],
        window_start=8.0,
        window_end=17.0,
    )
    assert refined is not None
    assert refined["transcript"].startswith("Перед ним")


@probe("highlights_recovers_unfinished_previous")
def _():
    fragment = {"start_seconds": 12.0, "end_seconds": 16.0}
    refined, evidence = refine_fragment_from_transcript(
        fragment,
        [
            _seg(9.4, 11.5, "Христианин должен помнить,"),
            _seg(11.65, 16.5, "земной мир не является его окончательным домом."),
        ],
        window_start=8.0,
        window_end=18.0,
    )
    assert refined is not None, evidence
    assert refined["transcript"].startswith("Христианин должен")


@probe("highlights_does_not_prepend_completed_previous")
def _():
    fragment = {"start_seconds": 12.0, "end_seconds": 16.5}
    refined, evidence = refine_fragment_from_transcript(
        fragment,
        [
            _seg(8.8, 11.3, "Предыдущая мысль полностью завершена."),
            _seg(11.65, 17.0, "Человек должен бодрствовать и твёрдо стоять в вере."),
        ],
        window_start=8.0,
        window_end=18.0,
    )
    assert refined is not None, evidence
    assert refined["transcript"].startswith("Человек должен")


@probe("highlights_rejects_unfinished_ending")
def _():
    fragment = {"start_seconds": 10.0, "end_seconds": 14.0}
    refined, _ = refine_fragment_from_transcript(
        fragment,
        [_seg(9.5, 14.5, "Он пишет: бодрствуйте и стойте в")],
        window_start=8.0,
        window_end=16.0,
    )
    assert refined is None


@probe("highlights_rejects_unbalanced_quote")
def _():
    fragment = {"start_seconds": 10.0, "end_seconds": 15.0}
    refined, _ = refine_fragment_from_transcript(
        fragment,
        [_seg(9.5, 15.5, "Проповедник сказал: «Бодрствуйте и стойте в вере.")],
        window_start=8.0,
        window_end=17.0,
    )
    assert refined is None


@probe("highlights_rejects_internal_silence")
def _():
    fragment = {"start_seconds": 10.0, "end_seconds": 20.0}
    refined, evidence = refine_fragment_from_transcript(
        fragment,
        [
            _seg(9.8, 12.0, "Проснитесь.", [{"start": 9.8, "end": 10.8, "word": "Проснитесь."}]),
            _seg(18.0, 20.2, "Время действовать.", [
                {"start": 18.0, "end": 18.5, "word": "Время"},
                {"start": 18.6, "end": 19.7, "word": "действовать."},
            ]),
        ],
        window_start=8.0,
        window_end=22.0,
    )
    assert refined is None
    assert evidence["reason"] == "internal_silence"


@probe("highlights_rejects_low_speech_coverage")
def _():
    fragment = {"start_seconds": 10.0, "end_seconds": 20.0}
    words = [
        {"start": 10.0, "end": 11.0, "word": "Нужно"},
        {"start": 14.0, "end": 15.0, "word": "твёрдо"},
        {"start": 18.0, "end": 19.0, "word": "стоять."},
    ]
    with _env(HIGHLIGHTS_MAX_INTERNAL_SILENCE_SECONDS="6", HIGHLIGHTS_MIN_SPEECH_COVERAGE="0.8"):
        refined, evidence = refine_fragment_from_transcript(
            fragment,
            [_seg(9.8, 20.2, "Нужно бодрствовать, молиться и твёрдо стоять в вере.", words)],
            window_start=9.0,
            window_end=21.0,
        )
    assert refined is None
    assert evidence["reason"] == "low_speech_coverage", evidence


@probe("highlights_rejects_too_short")
def _():
    fragment = {"start_seconds": 10.0, "end_seconds": 12.5}
    refined, evidence = refine_fragment_from_transcript(
        fragment,
        [_seg(9.9, 12.7, "Нужно бодрствовать и стоять в вере всегда.")],
        window_start=9.0,
        window_end=13.0,
    )
    assert refined is None
    assert evidence["reason"] == "too_short_after_refine"


@probe("highlights_rejects_too_long")
def _():
    fragment = {"start_seconds": 10.0, "end_seconds": 42.0}
    refined, evidence = refine_fragment_from_transcript(
        fragment,
        [_seg(9.9, 42.2, "Нужно бодрствовать, молиться и твёрдо стоять в вере каждый день.")],
        window_start=9.0,
        window_end=43.0,
    )
    assert refined is None
    assert evidence["reason"] == "too_long_after_refine"


@probe("highlights_drops_overlap")
def _():
    accepted, rejected = _drop_overlaps_and_repeats([
        {"start_seconds": 10.0, "end_seconds": 16.0, "transcript": "Нужно бодрствовать и стоять в вере."},
        {"start_seconds": 15.0, "end_seconds": 20.0, "transcript": "Другой фрагмент."},
    ])
    assert len(accepted) == 1
    assert rejected[0]["reason"] == "source_overlap"


@probe("highlights_drops_repeated_meaning")
def _():
    accepted, rejected = _drop_overlaps_and_repeats([
        {"start_seconds": 10.0, "end_seconds": 16.0, "transcript": "Нужно бодрствовать и твёрдо стоять в вере."},
        {"start_seconds": 30.0, "end_seconds": 36.0, "transcript": "Нужно твёрдо стоять в вере и бодрствовать."},
    ])
    assert len(accepted) == 1
    assert rejected[0]["reason"] == "repeated_meaning"


@probe("subtitles_map_across_fragments")
def _():
    mapped = build_delivery_subtitles([
        {"start_seconds": 10.0, "end_seconds": 14.0, "_subtitle_source_segments": [
            {"start": 10.5, "end": 12.0, "text": "Первый.", "words": []}
        ]},
        {"start_seconds": 30.0, "end_seconds": 35.0, "_subtitle_source_segments": [
            {"start": 31.0, "end": 33.0, "text": "Второй.", "words": []}
        ]},
    ])
    assert mapped[0]["start"] == 0.5
    assert mapped[1]["start"] == 5.0


@probe("subtitles_scale_speed_two")
def _():
    scaled = scale_subtitle_segments([
        {"start": 1.0, "end": 3.0, "text": "Текст", "words": [
            {"start": 1.2, "end": 1.8, "word": "Текст"}
        ]}
    ], 2.0)
    assert scaled[0]["start"] == 0.5
    assert scaled[0]["words"][0]["end"] == 0.9


@probe("subtitles_speed_one_identity")
def _():
    value = [{"start": 1.0, "end": 3.0, "text": "Текст", "words": []}]
    assert scale_subtitle_segments(value, 1.0) is value


@probe("probe_mapping_drops_separator_crossing")
def _():
    windows = [{"index": 0, "probe_start": 0.0, "probe_end": 10.0, "source_start": 100.0, "source_end": 110.0}]
    mapped = _map_probe_segments_to_source(
        [{"start": -0.6, "end": 1.0, "text": "Чужой контекст.", "words": []}], windows
    )
    assert mapped[0] == []


@probe("probe_mapping_clips_word_evidence")
def _():
    windows = [{"index": 0, "probe_start": 0.0, "probe_end": 10.0, "source_start": 100.0, "source_end": 110.0}]
    mapped = _map_probe_segments_to_source([
        {"start": -0.3, "end": 1.0, "text": "Лишнее Проснитесь.", "words": [
            {"start": -0.2, "end": -0.05, "word": "Лишнее"},
            {"start": 0.1, "end": 0.8, "word": "Проснитесь."},
        ]}
    ], windows)
    assert mapped[0][0]["text"] == "Проснитесь."
    assert mapped[0][0]["words"][0]["start"] == 100.1


@probe("telegraph_content_too_big_nonretryable")
def _():
    result = classify_telegraph_edit_error("CONTENT_TOO_BIG", status_code=200)
    assert not result.retryable
    assert telegraph_retry_delay(result, 0) == 0


@probe("telegraph_flood_wait_delay")
def _():
    result = classify_telegraph_edit_error("FLOOD_WAIT_7", status_code=200)
    assert result.retryable
    assert result.retry_after_seconds == 7
    assert telegraph_retry_delay(result, 0) == 7


@probe("telegraph_503_retryable")
def _():
    assert classify_telegraph_edit_error("upstream", status_code=503).retryable


@probe("telegraph_bad_payload_nonretryable")
def _():
    assert not classify_telegraph_edit_error("TITLE_REQUIRED", status_code=200).retryable


@probe("telegraph_page_path_url")
def _():
    assert telegraph_page_path("https://telegra.ph/Lyudi-Slova-08-02") == "Lyudi-Slova-08-02"


@probe("synopsis_scripture_not_timestamp")
def _():
    section = {"content": "Иеремия 12:5 и 1 Коринфянам 16:13–14.", "blocks": [{"text": "Иоанна 3:16"}]}
    assert section_anchor_seconds(section) == ()


@probe("synopsis_reconciles_earliest_anchor")
def _():
    sections = [{"title": "Первый", "time": "0:00", "content": "⏱ 0:10"}, {"title": "Второй", "time": "12:00", "content": "⏱ 10:34. ⏱ 11:20."}]
    reconciled, outline, _ = reconcile_synopsis_timestamps(sections)
    assert reconciled[1]["time"] == "10:34"
    assert outline[1]["time"] == "10:34"


@probe("synopsis_recovers_missing_start")
def _():
    reconciled, outline, _ = reconcile_synopsis_timestamps([
        {"title": "Раздел", "time": "", "content": "Точка входа ⏱ 5:40"}
    ], [])
    assert reconciled[0]["time"] == "5:40"
    assert outline[0]["time"] == "5:40"


@probe("synopsis_blocks_cross_boundary_guess")
def _():
    sections = [
        {"title": "Первый", "time": "10:00", "content": "⏱ 10:05"},
        {"title": "Второй", "time": "12:00", "content": "⏱ 9:50"},
    ]
    reconciled, _, issues = reconcile_synopsis_timestamps(sections)
    assert reconciled[1]["time"] == "12:00"
    assert issues[0].code == "section_time_reconcile_blocked"


@probe("synopsis_reconciliation_idempotent")
def _():
    sections = [{"title": "Раздел", "time": "8:00", "content": "⏱ 7:34"}]
    first_sections, first_outline, _ = reconcile_synopsis_timestamps(sections)
    second_sections, second_outline, second_issues = reconcile_synopsis_timestamps(first_sections, first_outline)
    assert first_sections == second_sections
    assert first_outline == second_outline
    assert second_issues == []


@probe("synopsis_outline_uses_reconciled_time")
def _():
    sections = [{"title": "Раздел", "time": "9:00", "content": "⏱ 8:34"}]
    reconciled, outline, _ = reconcile_synopsis_timestamps(sections, [{"title": "Старое", "time": "9:00"}])
    assert reconciled[0]["time"] == "8:34"
    assert outline == [{"title": "Раздел", "time": "8:34"}]


@probe("synopsis_unresolved_reports_inline_before_start")
def _():
    issues = unresolved_timestamp_issues([
        {"title": "Раздел", "time": "12:00", "content": "⏱ 11:00"}
    ])
    assert issues and issues[0].code == "inline_timestamp_before_section"


for marker in (
    "То есть",
    "Иными словами",
    "Другими словами",
    "Например",
    "Кроме того",
    "Более того",
    "Таким образом",
    "С другой стороны",
):
    @probe(f"discourse_marker_{marker.casefold().replace(' ', '_')}", severity="advisory")
    def _marker_probe(marker=marker):
        _dependent_start_probe(marker)


def main() -> int:
    hard_failures = 0
    advisory_failures = 0
    passes = 0
    for name, severity, fn in PROBES:
        try:
            fn()
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            if severity == "hard":
                hard_failures += 1
                status = "FAIL"
            else:
                advisory_failures += 1
                status = "WARN"
            print(f"PROBE|{status}|{severity}|{name}|{detail}")
            if os.getenv("MARATHON_TRACEBACKS") == "1":
                traceback.print_exc()
        else:
            passes += 1
            print(f"PROBE|PASS|{severity}|{name}|")
    print(
        f"PROBE_SUMMARY|total={len(PROBES)}|pass={passes}|"
        f"hard_fail={hard_failures}|advisory_fail={advisory_failures}"
    )
    return 1 if hard_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
