"""Build the publication context after analysis and before Telegraph rendering."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from converters.md_telegraph import get_caption_timestamp_limit
from core.database import asettings_get_all
from core.text_utils import normalize_author_name
from pipelines.publication_plan import (
    PagePlan,
    build_plan,
    has_ai_analysis,
    terms_total as _plan_terms_total,
)
from services.search import _build_search_title

logger = logging.getLogger(__name__)

SettingsGetAll = Callable[[], Awaitable[Mapping[str, Any] | None]]


@dataclass(frozen=True)
class PublicationContext:
    """Values shared by rendering, alternative search, and the final caption."""

    search_title: str
    tg_author: str
    questions: Any
    terms_data: Any
    format_name: str
    timestamps_total: int
    plan: PagePlan


async def prepare_publication_context(
    *,
    ai_data: dict[str, Any] | None,
    degraded: Mapping[str, Any] | None,
    full_title: str,
    performer: str,
    media_id: str,
    telegraph_url: str | None,
    settings_get_all: SettingsGetAll = asettings_get_all,
) -> PublicationContext:
    """Preserve the historical planning order and its diagnostics."""
    search_title = _build_search_title(ai_data, full_title)
    author = normalize_author_name(
        (ai_data or {}).get("real_author") or performer
    ) or performer
    questions = (ai_data or {}).get("questions", []) if ai_data else []
    terms_data = (
        (ai_data or {}).get("terms_data", {}) or {} if ai_data else {}
    )

    # Keep the single batch settings read ahead of caption-source metrics.
    _all_settings = await settings_get_all()
    caption_source_data = ai_data or degraded
    format_name = (caption_source_data or {}).get("format", "other")
    timestamps_total = len([
        line
        for line in ((caption_source_data or {}).get("timestamps") or "").split("\n")
        if line.strip()
    ])
    timestamp_cap = get_caption_timestamp_limit(format_name)

    terms_total = _plan_terms_total(terms_data)
    _flags, plan = build_plan(_all_settings, ai_data=ai_data)
    # «Анализ есть» и «пользователь всё выключил» — разные ситуации.
    analysis_available = has_ai_analysis(ai_data)
    if not analysis_available:
        logger.warning(
            "AI analysis is missing/invalid for %s — skipping AI-derived "
            "Telegraph pages to avoid hallucinated partial publications",
            media_id,
        )
    is_qa = format_name == "qa"
    logger.info(
        f"format={format_name} is_qa={is_qa} ts_total={timestamps_total} "
        f"ts_cap={timestamp_cap} synopsis={'ok' if telegraph_url else 'none'} "
        f"terms_items={terms_total} "
        f"(concepts={len(terms_data.get('concepts', []))} "
        f"scripture={len(terms_data.get('scripture', []))} "
        f"transl={len(terms_data.get('translations', []))} "
        f"lex={len(terms_data.get('lexicon_notes', []))}) "
        f"| feat: analytics={plan.analytics} questions={plan.questions} "
        f"terms={plan.terms} study_analysis={plan.study_analysis} "
        f"reflection={plan.reflection_application}"
    )
    return PublicationContext(
        search_title=search_title,
        tg_author=author,
        questions=questions,
        terms_data=terms_data,
        format_name=format_name,
        timestamps_total=timestamps_total,
        plan=plan,
    )
