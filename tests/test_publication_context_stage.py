"""Behavioral contracts for the post-analysis publication context."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

from pipelines.publication_context_stage import prepare_publication_context


def test_analysis_context_preserves_batch_plan_metrics_and_diagnostics(caplog):
    settings = {
        "analytics": True,
        "questions": True,
        "terms": True,
        "study_analysis": False,
        "reflection_application": False,
    }
    settings_get_all = AsyncMock(return_value=settings)
    questions = {"blocks": [{"text": "Question"}]}
    terms_data = {
        "concepts": ["one", "two"],
        "scripture": ["Psalm"],
        "translations": [],
        "lexicon_notes": ["note"],
    }
    ai_data = {
        "real_event": "Conference",
        "real_title": "A sufficiently long lecture title",
        "real_author": "John Smith",
        "questions": questions,
        "terms_data": terms_data,
        "format": "qa",
        "timestamps": "00:00 Intro\n\n00:10 Topic\n",
    }

    with caplog.at_level(logging.INFO):
        context = asyncio.run(
            prepare_publication_context(
                ai_data=ai_data,
                degraded={"format": "sermon", "timestamps": "ignored"},
                full_title="Fallback title",
                performer=None,
                media_id="media-id",
                telegraph_url="",
                settings_get_all=settings_get_all,
            )
        )

    settings_get_all.assert_awaited_once_with()
    assert context.search_title == ("Conference - A sufficiently long lecture title - John Smith")
    assert context.tg_author == "John Smith"
    assert context.questions is questions
    assert context.terms_data is terms_data
    assert context.format_name == "qa"
    assert context.timestamps_total == 2
    assert context.plan.analytics is True
    assert context.plan.questions is True
    assert context.plan.terms is True
    assert context.plan.study_analysis is False
    assert context.plan.reflection_application is False
    assert (
        "format=qa is_qa=True ts_total=2 ts_cap=999 synopsis=none "
        "terms_items=4 (concepts=2 scripture=1 transl=0 lex=1) "
        "| feat: analytics=True questions=True terms=True "
        "study_analysis=False reflection=False"
    ) in caplog.text
    assert "AI analysis is missing/invalid" not in caplog.text


def test_degraded_metrics_do_not_become_ai_pages_or_analysis_sections(caplog):
    settings_get_all = AsyncMock(
        return_value={
            "analytics": True,
            "questions": True,
            "terms": True,
            "study_analysis": True,
            "reflection_application": True,
        }
    )
    degraded = {
        "real_author": "Ignored degraded author",
        "questions": {"blocks": [{"text": "Must not leak"}]},
        "terms_data": {"concepts": ["Must not leak"]},
        "format": "sermon",
        "timestamps": " first \n\n second\nthird",
    }

    with caplog.at_level(logging.INFO):
        context = asyncio.run(
            prepare_publication_context(
                ai_data=None,
                degraded=degraded,
                full_title="Fallback title",
                performer="Fallback Performer",
                media_id="degraded-media",
                telegraph_url="https://telegra.ph/synopsis",
                settings_get_all=settings_get_all,
            )
        )

    settings_get_all.assert_awaited_once_with()
    assert context.search_title == "Fallback title"
    assert context.tg_author == "Fallback Performer"
    assert context.questions == []
    assert context.terms_data == {}
    assert context.format_name == "sermon"
    assert context.timestamps_total == 3
    assert not any(
        (
            context.plan.analytics,
            context.plan.questions,
            context.plan.terms,
            context.plan.study_analysis,
            context.plan.reflection_application,
        )
    )
    assert (
        "AI analysis is missing/invalid for degraded-media — skipping "
        "AI-derived Telegraph pages to avoid hallucinated partial publications"
    ) in caplog.text
    assert (
        "format=sermon is_qa=False ts_total=3 ts_cap=30 synopsis=ok "
        "terms_items=0 (concepts=0 scripture=0 transl=0 lex=0) "
        "| feat: analytics=False questions=False terms=False "
        "study_analysis=False reflection=False"
    ) in caplog.text
