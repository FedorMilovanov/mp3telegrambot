"""Prepare the shared LiveDub publication card and HTML title line."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NamedTuple

ValueFn = Callable[[], Any]


class LiveDubPublication(NamedTuple):
    """Values consumed by every subsequent LiveDub delivery branch."""

    card: dict[str, Any]
    title_line: str
    title_html: str


async def prepare_livedub_publication(
    *,
    ai_data: ValueFn,
    cached_ai_data: ValueFn,
    title: str,
    performer: str,
    full_title: str,
    source_url: str,
    html_module: Any,
) -> LiveDubPublication:
    """Resolve fresh/cached AI data lazily and build the publication title."""
    publication_ai = None
    try:
        candidate = ai_data()
        publication_ai = candidate if isinstance(candidate, dict) else None
    except NameError:
        publication_ai = None
    if publication_ai is None:
        try:
            candidate = cached_ai_data()
            publication_ai = candidate if isinstance(candidate, dict) else None
        except NameError:
            publication_ai = None

    publication_title = str((publication_ai or {}).get("real_title") or title or "").strip()
    publication_author = str((publication_ai or {}).get("real_author") or performer or "").strip()
    source_line = (
        f"{publication_title} - {publication_author}"
        if publication_author
        else (publication_title or full_title or "Переведённое видео")
    )

    # Keep the service import at call time, alongside the historical sender.
    from services.livedub_publication import build_publication_card

    card = await build_publication_card(source_line, source_url)
    public_title = str(card.get("title") or publication_title or "Переведённое видео").strip()
    public_author = str(card.get("author") or publication_author or "").strip()
    title_line = (
        f"{public_title} - {public_author}"
        if public_author and public_author.casefold() not in public_title.casefold()
        else public_title
    )
    title_html = html_module.escape(title_line) if title_line else ""
    return LiveDubPublication(card=card, title_line=title_line, title_html=title_html)
