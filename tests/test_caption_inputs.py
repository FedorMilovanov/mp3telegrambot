#!/usr/bin/env python3
"""Данные подписи: приоритет ссылок, статус публикации, покрытие таймкодами."""

from __future__ import annotations

import pytest

from core.publication_status import PublicationStatus
from pipelines.caption_inputs import (
    build_caption_base,
    build_caption_inputs,
    resolve_page_links,
    with_timestamp_coverage,
)

STUDY = "https://telegra.ph/razbor-1"
REFL = "https://telegra.ph/razmyshlenie-1"
ANALYTICS = "https://telegra.ph/analitika-1"
QUESTIONS = "https://telegra.ph/voprosy-1"
TERMS = "https://telegra.ph/terminy-1"

LIMITS = {"sermon": 30, "qa": 999, "other": 20}


def limit(format_name: str) -> int:
    return LIMITS.get(format_name, LIMITS["other"])


# ── Ссылки ───────────────────────────────────────────────────────────────


def test_new_pages_take_priority_over_legacy() -> None:
    links = resolve_page_links(study=STUDY, reflection=REFL, analytics=ANALYTICS, questions=QUESTIONS, terms=TERMS)
    assert links.quotes == STUDY
    assert links.questions == REFL
    assert links.terms == ""


def test_legacy_links_used_when_new_pages_absent() -> None:
    links = resolve_page_links(analytics=ANALYTICS, questions=QUESTIONS, terms=TERMS)
    assert links == type(links)(quotes=ANALYTICS, questions=QUESTIONS, terms=TERMS)


def test_terms_kept_when_only_one_new_page() -> None:
    assert resolve_page_links(study=STUDY, terms=TERMS).terms == TERMS
    assert resolve_page_links(reflection=REFL, terms=TERMS).terms == TERMS


def test_empty_links_stay_empty() -> None:
    links = resolve_page_links()
    assert (links.quotes, links.questions, links.terms) == ("", "", "")


# ── Покрытие таймкодами ──────────────────────────────────────────────────


def test_no_analysis_stays_none() -> None:
    assert with_timestamp_coverage(None, 900) is None
    assert with_timestamp_coverage({}, 900) == {}


def test_short_recording_is_always_complete() -> None:
    """До 10 минут частичное покрытие не считается дефектом."""
    data = with_timestamp_coverage({"timestamps": "00:00 вступление"}, 300)
    assert data is not None
    assert data["segments_status"] == "complete"
    assert data["timestamp_coverage_ratio"] == pytest.approx(0.0)


def test_long_recording_with_low_coverage_is_partial() -> None:
    data = with_timestamp_coverage({"timestamps": "00:00 вступление\n05:00 тема"}, 1200)
    assert data is not None
    assert data["segments_status"] == "partial"
    assert data["timestamp_coverage_ratio"] == pytest.approx(0.25)


def test_long_recording_with_good_coverage_is_complete() -> None:
    data = with_timestamp_coverage({"timestamps": "00:00 a\n25:00 b"}, 1200)
    assert data is not None
    assert data["segments_status"] == "complete"


def test_warning_marks_partial_for_long_recording() -> None:
    data = with_timestamp_coverage({"timestamps": "29:00 конец", "timestamp_coverage_warning": True}, 1200)
    assert data is not None
    assert data["segments_status"] == "partial"


def test_warning_ignored_for_short_recording() -> None:
    data = with_timestamp_coverage({"timestamps": "04:00", "timestamp_coverage_warning": True}, 300)
    assert data is not None
    assert data["segments_status"] == "complete"


def test_source_dict_not_mutated() -> None:
    source = {"timestamps": "00:00 a"}
    with_timestamp_coverage(source, 1200)
    assert source == {"timestamps": "00:00 a"}


# ── Данные подписи ───────────────────────────────────────────────────────

OK_STATUS = PublicationStatus(status="complete", missing=(), warning="")
PARTIAL_STATUS = PublicationStatus(status="partial", missing=("reflection",), warning="Не вышло: размышление.")


def test_trims_timestamp_preview_when_over_limit() -> None:
    base = build_caption_base(
        ai_data={"timestamps": "x"},
        degraded=None,
        format_name="sermon",
        timestamps_total=42,
        timestamp_limit=limit,
        publication=OK_STATUS,
    )
    assert base is not None
    assert base["_caption_timestamps_trimmed"] is True
    assert base["_caption_timestamps_total"] == 42
    assert base["_caption_timestamps_shown"] == 30


def test_no_preview_when_within_limit() -> None:
    base = build_caption_base(
        ai_data={"timestamps": "x"},
        degraded=None,
        format_name="sermon",
        timestamps_total=10,
        timestamp_limit=limit,
        publication=OK_STATUS,
    )
    assert base is not None
    assert "_caption_timestamps_trimmed" not in base


def test_partial_publication_warning_requires_analysis() -> None:
    base = build_caption_base(
        ai_data={"timestamps": "x"},
        degraded=None,
        format_name="sermon",
        timestamps_total=0,
        timestamp_limit=limit,
        publication=PARTIAL_STATUS,
    )
    assert base is not None
    assert base["_partial_publication_warning"] == PARTIAL_STATUS.warning


def test_degraded_base_receives_preview_but_not_publication_warning() -> None:
    base = build_caption_base(
        ai_data=None,
        degraded={"_ai_unavailable_warning": "нет Gemini", "timestamps": "x"},
        format_name="other",
        timestamps_total=25,
        timestamp_limit=limit,
        publication=PARTIAL_STATUS,
    )
    assert base is not None
    assert base["_caption_timestamps_shown"] == 20
    assert "_partial_publication_warning" not in base


def test_base_none_when_nothing_known() -> None:
    assert (
        build_caption_base(
            ai_data=None,
            degraded=None,
            format_name="other",
            timestamps_total=0,
            timestamp_limit=limit,
            publication=OK_STATUS,
        )
        is None
    )


# ── Оркестратор ──────────────────────────────────────────────────────────


def test_inputs_combine_links_status_and_base() -> None:
    ai = {"timestamps": "00:00 a\n25:00 b"}
    inputs = build_caption_inputs(
        ai_data=ai,
        degraded=None,
        duration=1200,
        format_name="sermon",
        timestamps_total=2,
        timestamp_limit=limit,
        study=STUDY,
        reflection=REFL,
        analytics=ANALYTICS,
        questions=QUESTIONS,
        terms=TERMS,
        synopsis="https://telegra.ph/konspekt-1",
        plan_study=True,
        plan_reflection=False,
        rutube_url="https://rt/1",
        vk_url=None,
    )

    assert inputs.links.quotes == STUDY
    assert inputs.links.questions == REFL
    assert inputs.rutube_url == "https://rt/1"
    assert inputs.vk_url == ""
    assert inputs.ai_data is not None
    assert inputs.ai_data["segments_status"] == "complete"
    assert inputs.publication.status == "complete"
    assert inputs.caption_base is not None


def test_unplanned_page_does_not_make_publication_partial() -> None:
    """Размышление не планировали — его отсутствие не дефект."""
    inputs = build_caption_inputs(
        ai_data={"timestamps": "00:00"},
        degraded=None,
        duration=600,
        format_name="other",
        timestamps_total=1,
        timestamp_limit=limit,
        study=STUDY,
        synopsis="https://telegra.ph/konspekt-1",
        plan_study=True,
        plan_reflection=False,
    )
    assert inputs.publication.status == "complete"
    assert inputs.publication.missing == ()


def test_missing_expected_page_is_reported_and_flagged_in_caption() -> None:
    inputs = build_caption_inputs(
        ai_data={"timestamps": "00:00"},
        degraded=None,
        duration=600,
        format_name="other",
        timestamps_total=1,
        timestamp_limit=limit,
        synopsis="https://telegra.ph/konspekt-1",
        plan_reflection=True,
    )
    assert inputs.publication.status == "partial"
    assert "reflection" in inputs.publication.missing
    assert inputs.caption_base is not None
    assert inputs.caption_base["_partial_publication_warning"] == inputs.publication.warning


def test_degraded_inputs_have_no_expectations() -> None:
    """Без анализа страницы не ожидались: публикация не «частичная», её нет."""
    inputs = build_caption_inputs(
        ai_data=None,
        degraded={"real_title": "Т", "_ai_unavailable_warning": "нет Gemini"},
        duration=600,
        format_name="other",
        timestamps_total=0,
        timestamp_limit=limit,
    )
    # Ни одна страница не ожидалась, поэтому статус — «unknown», а не «failed»:
    # «нет анализа» и «получилось не всё» — разные ситуации.
    assert inputs.publication.status == "unknown"
    assert inputs.publication.missing == ()
    assert inputs.publication.warning == ""
    assert inputs.caption_base is not None
    assert inputs.caption_base["_ai_unavailable_warning"] == "нет Gemini"
