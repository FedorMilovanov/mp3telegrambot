#!/usr/bin/env python3
"""Portable Telegram/YouTube publication captions for Shorts Factory MAX."""
from __future__ import annotations

import copy
import html
from typing import Any, Callable

_CONTEXT_FIELD = "_factory_publication_source"
_BASE_CAPTION_LIMIT = 690
_INSTALLED = False


def _source_interval(candidate: dict[str, Any]) -> tuple[float, float]:
    """Return the original YouTube semantic clock, not shifted LiveDub render time."""
    pairs = (
        ("source_start_seconds", "source_end_seconds"),
        ("livedub_semantic_start_seconds", "livedub_semantic_end_seconds"),
        ("start_seconds", "end_seconds"),
    )
    for start_key, end_key in pairs:
        if start_key not in candidate or end_key not in candidate:
            continue
        try:
            start = float(candidate.get(start_key) or 0.0)
            end = float(candidate.get(end_key) or 0.0)
        except (TypeError, ValueError, OverflowError):
            continue
        if end > start >= 0:
            return start, end
    return 0.0, 0.0


def _timestamp(value: float) -> str:
    total = max(0, int(round(float(value or 0.0))))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _trim_words(value: str, limit: int) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    if limit < 8:
        return ""
    clipped = text[: limit - 1].rstrip()
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0].rstrip()
    return clipped + "…" if clipped else ""


def _context_candidates(
    candidates: list[dict[str, Any]],
    context: dict[str, str],
) -> list[dict[str, Any]]:
    output = copy.deepcopy(candidates or [])
    for item in output:
        if isinstance(item, dict):
            item[_CONTEXT_FIELD] = copy.deepcopy(context)
    return output


def _plain_caption(
    *,
    candidate: dict[str, Any],
    performer: str,
    real_author: str,
    yt_url: str,
    title_limit: int | None = None,
    hashtags: list[str] | None = None,
) -> str:
    context = candidate.get(_CONTEXT_FIELD)
    context = context if isinstance(context, dict) else {}
    fragment_title = _clean_text(candidate.get("title") or candidate.get("hook"))
    if title_limit is not None:
        fragment_title = _trim_words(fragment_title, title_limit)
    author = _clean_text(real_author or performer)
    if fragment_title and author:
        first_line = f"{fragment_title} — {author}"
    else:
        first_line = fragment_title or author or "Фрагмент проповеди"

    source_title = _clean_text(context.get("source_full_title"))
    source_url = _clean_text(context.get("source_url") or yt_url)
    start, end = _source_interval(candidate)
    source_lines = []
    if source_title:
        source_lines.append(f"🎙 Полная проповедь: «{source_title}»")
    source_lines.append(
        "⏱ Фрагмент в полной проповеди: "
        f"{_timestamp(start)}–{_timestamp(end)}"
    )
    if source_url:
        source_lines.append(f"▶️ {source_url}")

    tag_values = hashtags
    if tag_values is None:
        tag_values = [
            _clean_text(tag)
            for tag in (candidate.get("hashtags") or [])
            if _clean_text(tag)
        ][:4]

    parts = [first_line, "\n".join(source_lines)]
    if tag_values:
        parts.append(" ".join(tag_values))
    return "\n\n".join(part for part in parts if part)


def build_factory_portable_caption(
    *,
    candidate: dict[str, Any],
    performer: str = "",
    real_author: str = "",
    yt_url: str = "",
) -> str:
    """Build a compact base caption while reserving room for publication prose."""
    tags = [
        _clean_text(tag)
        for tag in (candidate.get("hashtags") or [])
        if _clean_text(tag)
    ][:4]
    plain = _plain_caption(
        candidate=candidate,
        performer=performer,
        real_author=real_author,
        yt_url=yt_url,
        hashtags=tags,
    )
    while len(plain) > _BASE_CAPTION_LIMIT and tags:
        tags.pop()
        plain = _plain_caption(
            candidate=candidate,
            performer=performer,
            real_author=real_author,
            yt_url=yt_url,
            hashtags=tags,
        )

    if len(plain) > _BASE_CAPTION_LIMIT:
        context = candidate.get(_CONTEXT_FIELD)
        context = context if isinstance(context, dict) else {}
        source_title = _clean_text(context.get("source_full_title"))
        source_url = _clean_text(context.get("source_url") or yt_url)
        start, end = _source_interval(candidate)
        source_block = "\n".join(
            part
            for part in (
                f"🎙 Полная проповедь: «{source_title}»" if source_title else "",
                (
                    "⏱ Фрагмент в полной проповеди: "
                    f"{_timestamp(start)}–{_timestamp(end)}"
                ),
                f"▶️ {source_url}" if source_url else "",
            )
            if part
        )
        author = _clean_text(real_author or performer)
        header_budget = max(
            0,
            _BASE_CAPTION_LIMIT - len(source_block) - 2,
        )
        if author and header_budget > len(author) + 3:
            title_budget = header_budget - len(author) - 3
            title = _trim_words(
                _clean_text(candidate.get("title") or candidate.get("hook")),
                title_budget,
            )
            header = f"{title} — {author}" if title else author
        else:
            header = _trim_words(
                _clean_text(candidate.get("title") or candidate.get("hook")),
                header_budget,
            )
        plain = "\n\n".join(part for part in (header, source_block) if part)

    # Existing pipelines keep parse_mode=HTML. Escaping preserves plain copied text.
    return html.escape(plain, quote=False)


def _candidate_from_call(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    value = kwargs.get("candidate") if "candidate" in kwargs else (args[0] if args else None)
    return value if isinstance(value, dict) else {}


def _call_value(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    name: str,
    position: int,
) -> str:
    if name in kwargs:
        return str(kwargs.get(name) or "")
    if len(args) > position:
        return str(args[position] or "")
    return ""


def wrap_factory_portable_builder(builder: Callable[..., str]) -> Callable[..., str]:
    if getattr(builder, "_factory_portable_caption", False):
        return builder

    def wrapped(*args, **kwargs):
        candidate = _candidate_from_call(args, kwargs)
        context = candidate.get(_CONTEXT_FIELD)
        if not isinstance(context, dict):
            return builder(*args, **kwargs)
        return build_factory_portable_caption(
            candidate=candidate,
            performer=_call_value(args, kwargs, "performer", 1),
            real_author=_call_value(args, kwargs, "real_author", 2),
            yt_url=(
                _clean_text(context.get("source_url"))
                or _call_value(args, kwargs, "yt_url", 5)
            ),
        )

    wrapped._factory_portable_caption = True  # type: ignore[attr-defined]
    return wrapped


def install_factory_portable_publication() -> bool:
    """Patch only the Factory render context and caption-builder seams."""
    global _INSTALLED
    if _INSTALLED:
        return True

    import pipelines.clips as clips_module
    import pipelines.shorts as shorts_module
    import pipelines.shorts_factory as factory_module
    import services.shorts_factory_runtime as runtime_module
    from services.shorts_factory_video_quality import current_factory_source_metadata

    original_context = factory_module.factory_render_context

    def portable_context(
        shorts_candidates: list[dict[str, Any]],
        long_candidates: list[dict[str, Any]],
    ) -> Any:
        context = current_factory_source_metadata()
        shorts = _context_candidates(shorts_candidates, context)
        longs = _context_candidates(long_candidates, context)
        return original_context(shorts, longs)

    factory_module.factory_render_context = portable_context
    runtime_module.factory_render_context = portable_context
    shorts_module.build_short_caption = wrap_factory_portable_builder(
        shorts_module.build_short_caption
    )
    clips_module.build_clip_caption = wrap_factory_portable_builder(
        clips_module.build_clip_caption
    )

    _INSTALLED = True
    return True


__all__ = [
    "_CONTEXT_FIELD",
    "build_factory_portable_caption",
    "install_factory_portable_publication",
    "wrap_factory_portable_builder",
]
