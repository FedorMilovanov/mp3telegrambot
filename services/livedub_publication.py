#!/usr/bin/env python3
"""User-facing publication card for LiveDub video and MP3 results.

The explicit LiveDub delivery path calls these source-owned helpers before sending
video and MP3 results. No Telegram methods, info-card functions or pipeline modules
are intercepted or rebound at runtime.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
_PUBLICATION_CACHE: dict[str, dict[str, Any]] = {}

_LIVEDUB_MARKERS = (
    "живые голоса яндекса",
    "перевод яндекса (обычные голоса)",
)
_AUDIO_MARKERS = (
    "аудиоверсия русского перевода яндекса",
    "чистая аудиодорожка русского перевода яндекса",
    "аудиоверсия финального дубляжа",
    "русская аудиоверсия",
)


def _enabled() -> bool:
    return os.getenv("LIVEDUB_PUBLICATION_CARD", "1").strip().lower() in _TRUE


def _plain(value: Any, limit: int = 1000) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip(" \t\r\n-—–|;:")[:limit].strip()


def _source_url(value: Any = "") -> str:
    candidate = _plain(value, 600)
    if not re.match(r"^https?://", candidate, flags=re.IGNORECASE):
        return ""
    return candidate


def _cache_key(source_line: str, source_url: str = "") -> str:
    return (_source_url(source_url) or _plain(source_line, 320)).casefold()


def _is_livedub_video_caption(value: Any) -> bool:
    text = _plain(value, 1000).casefold()
    return any(marker in text for marker in _LIVEDUB_MARKERS)


def _is_livedub_audio_caption(value: Any) -> bool:
    text = _plain(value, 500).casefold()
    return any(marker in text for marker in _AUDIO_MARKERS)


def _bold_title_line(caption: str) -> str:
    match = re.search(r"<b>(.*?)</b>", str(caption or ""), flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return _plain(match.group(1), 300)


def _canonical_title(value: str) -> str:
    text = _plain(value, 220)
    if not text:
        return ""
    try:
        from core.text_utils import normalize_title_text, title_case_fragment

        text = normalize_title_text(text) or text
        return title_case_fragment(text).strip()
    except Exception:
        return text[:1].upper() + text[1:]


def _canonical_author(value: str) -> str:
    text = _plain(value, 120)
    if not text:
        return ""
    try:
        from core.person_names import canonical_person_name, known_author_from_text

        return canonical_person_name(known_author_from_text(text) or text)
    except Exception:
        return text


def _split_title_author(line: str) -> tuple[str, str]:
    try:
        from services.livedub_output_policy import _split_title_author as split

        title, author = split(line)
        return _plain(title, 220), _canonical_author(author)
    except Exception:
        return _plain(line, 220), ""


def _clean_description(value: Any) -> str:
    text = _plain(value, 650)
    text = re.sub(r"^[🎙️🎧📖💬✨🔥]+\s*", "", text).strip()
    text = re.sub(
        r"(?i)\b(?:живые голоса яндекса|русская аудиоверсия|перевод яндекса)\b[.:—–-]*\s*",
        "",
        text,
    ).strip()
    return text


def _fallback_description(title: str, author: str) -> str:
    """Use metadata-only wording; never invent a sermon thesis in fallback."""
    title = _canonical_title(title) or "этот материал"
    author = _canonical_author(author)
    if author:
        return f"Материал {author} на тему «{title}»."
    return f"Материал на тему «{title}»."


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "author": {"type": "string"},
            "description": {"type": "string"},
        },
        "required": ["title", "author", "description"],
    }


async def _generate_quality_publication(source_line: str) -> dict[str, str] | None:
    """Translate metadata and write restrained user copy on Gemini 3.7/HIGH."""
    source = _plain(source_line, 320)
    if not source:
        return None
    try:
        from core.globals import GEMINI_CLIENTS, make_text_config_smart
        from services.livedub_info import DEFAULT_INFO_MODEL
    except Exception:
        return None
    if not GEMINI_CLIENTS:
        return None

    model = DEFAULT_INFO_MODEL
    prompt = f"""
Подготовь краткую публикационную карточку для русскоязычного Telegram.
Исходная строка — название христианского видео и, возможно, имя автора.
Верни строго JSON: {{"title":"...","author":"...","description":"..."}}.

Правила:
- title обязательно переведи на естественный русский язык, без кликбейта и отсебятины;
- формат русского title: каждое значимое слово с заглавной буквы, но предлоги,
  союзы и частицы внутри названия со строчной: «Борьба с Искушением и Грехом»;
- author вынеси отдельно и используй принятое русское написание имени;
- description: 1–2 живых предложения, 140–360 знаков, только то, что надёжно
  следует из названия; не выдумывай тезисы, цитаты, места Писания, события или выводы;
- не пиши «в этом видео», «русская аудиоверсия», «перевод Яндекса», «ИИ»;
- не добавляй ссылку и эмодзи — оформление добавит бот;
- Tim Conway = Тим Конвей; R.C. Sproul = Р. Ч. Спроул;
  John MacArthur = Джон МакАртур; Paul Washer = Пол Вошер.

Исходная строка: {source}
""".strip()

    for client_index, client in enumerate(GEMINI_CLIENTS):
        try:
            cfg = make_text_config_smart(
                max_output_tokens=1200,
                model_name=model,
                thinking_level="high",
                response_mime_type="application/json",
                response_schema=_response_schema(),
            )
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=cfg,
                ),
                timeout=90.0,
            )
            raw = str(getattr(response, "text", "") or "").strip()
            raw = re.sub(
                r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE
            ).strip()
            data = json.loads(raw)
            title = _canonical_title(str(data.get("title") or ""))
            author = _canonical_author(str(data.get("author") or ""))
            description = _clean_description(data.get("description"))
            if title and re.search(r"[А-Яа-яЁё]", title) and description:
                return {
                    "title": title,
                    "author": author,
                    "description": description,
                    "model": model,
                }
        except Exception as exc:
            logger.info(
                "[LiveDubPublication] model=%s client=%d failed: %s",
                model,
                client_index,
                str(exc)[:140],
            )
    return None


async def build_publication_card(source_line: str, source_url: str = "") -> dict[str, Any]:
    url = _source_url(source_url)
    key = _cache_key(source_line, url)
    cached = _PUBLICATION_CACHE.get(key)
    if cached:
        return dict(cached)

    original_title, original_author = _split_title_author(source_line)
    generated = await _generate_quality_publication(source_line)
    if generated:
        title = generated["title"]
        author = generated.get("author") or original_author
        description = generated["description"]
        model = generated.get("model", "")
    else:
        title, author = original_title, original_author
        title = _canonical_title(title)
        author = _canonical_author(author)
        description = _fallback_description(title, author)
        model = "deterministic_fallback"

    card: dict[str, Any] = {
        "title": _canonical_title(title) or "Переведённое видео",
        "author": _canonical_author(author),
        "description": _clean_description(description) or _fallback_description(title, author),
        "source_url": url,
        "model": model,
    }
    _PUBLICATION_CACHE[key] = dict(card)
    # URL is the strongest key, but also save by final title for the following MP3
    # call when Telegram metadata arrives without the original source line.
    _PUBLICATION_CACHE.setdefault(_cache_key(card["title"], url), dict(card))
    return card


def _title_line(card: dict[str, Any]) -> str:
    title = _canonical_title(str(card.get("title") or ""))
    author = _canonical_author(str(card.get("author") or ""))
    if title and author and author.casefold() not in title.casefold():
        return f"{title} - {author}"
    return title or author or "Переведённое видео"


def _operational_notes(caption: str) -> list[str]:
    notes: list[str] = []
    for line in str(caption or "").splitlines():
        visible = _plain(line, 600)
        if not visible:
            continue
        low = visible.casefold()
        if any(marker in low for marker in _LIVEDUB_MARKERS):
            continue
        if visible.startswith(("⚠️", "🩹", "🔍")):
            notes.append(html.escape(visible, quote=False))
    return notes[:4]


def format_video_caption(card: dict[str, Any], original_caption: str = "") -> str:
    lines = [f"<b>{html.escape(_title_line(card), quote=False)}</b>"]
    description = _clean_description(card.get("description"))
    if description:
        lines += ["", f"🎙️ {html.escape(description, quote=False)}"]
    source = _source_url(card.get("source_url"))
    if source:
        lines += ["", f'🔗 <a href="{html.escape(source, quote=True)}">Оригинал видео</a>']
    notes = _operational_notes(original_caption)
    if notes:
        lines += ["", *notes]
    text = "\n".join(lines).strip()
    try:
        from converters.md_telegraph import safe_trim_caption

        return safe_trim_caption(text, 3900)
    except Exception:
        return text[:3900]


def format_audio_caption(card: dict[str, Any]) -> str:
    lines: list[str] = []
    description = _clean_description(card.get("description"))
    if description:
        lines.append(f"🎙️ {html.escape(description, quote=False)}")
    source = _source_url(card.get("source_url"))
    if source:
        if lines:
            lines.append("")
        lines.append(f'🔗 <a href="{html.escape(source, quote=True)}">Оригинал видео</a>')
    text = "\n".join(lines).strip()
    try:
        from converters.md_telegraph import safe_trim_caption

        return safe_trim_caption(text, 1000)
    except Exception:
        return text[:1000]
