#!/usr/bin/env python3
"""User-facing publication card for LiveDub video and MP3 results.

The internal pipeline needs provider markers so the audio companion can recognise a
successful LiveDub send. Users do not need those implementation labels. This
adapter is installed between ``livedub_output_policy`` and the audio companion:

* the outer companion still sees the private marker;
* the Telegram API receives a Russian title and description generated on the
  source-owned Gemini 3.6/HIGH semantic route, plus a link to the source video;
* the MP3 caption contains the useful description/link only — never labels such
  as ``Русская аудиоверсия`` or ``Живые голоса Яндекса``;
* the old separate ENG Quick info card is satisfied from the same cache and
  suppressed, so there is one polished publication block rather than duplicates.
"""
from __future__ import annotations

import asyncio
import contextvars
import html
import json
import logging
import os
import re
import threading
from typing import Any

logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
_INSTALL_LOCK = threading.Lock()
_CURRENT_SOURCE_URL: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mp3bot_livedub_source_url", default=""
)
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
    candidate = _plain(value or _CURRENT_SOURCE_URL.get(), 600)
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
    """Translate metadata and write restrained user copy on Gemini 3.6/HIGH."""
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
        try:
            from services.livedub_output_policy import _translate_title_line

            translated = await _translate_title_line(source_line)
            if translated:
                title, translated_author = translated
                author = translated_author or author
        except Exception as exc:
            logger.info("[LiveDubPublication] title fallback failed: %s", str(exc)[:140])
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


def _install_source_context() -> None:
    import pipelines.main_pipeline as pipeline

    original = pipeline.process_single_video
    if getattr(original, "_mp3bot_publication_context", False):
        return

    async def wrapped(url, *args, **kwargs):
        token = _CURRENT_SOURCE_URL.set(_source_url(url))
        try:
            return await original(url, *args, **kwargs)
        finally:
            _CURRENT_SOURCE_URL.reset(token)

    wrapped._mp3bot_publication_context = True  # type: ignore[attr-defined]
    pipeline.process_single_video = wrapped

    # Both modules import the function by value, so updating only the pipeline
    # module would leave the real message and playlist handlers on the old callable.
    try:
        import handlers.commands as commands

        if getattr(commands, "process_single_video", None) is original:
            commands.process_single_video = wrapped
    except Exception as exc:
        logger.debug("[LiveDubPublication] command binding patch skipped: %s", exc)
    try:
        import pipelines.playlist as playlist

        if getattr(playlist, "process_single_video", None) is original:
            playlist.process_single_video = wrapped
    except Exception as exc:
        logger.debug("[LiveDubPublication] playlist binding patch skipped: %s", exc)


def _wrap_send_video(cls: type) -> None:
    original = getattr(cls, "send_video", None)
    if original is None or getattr(original, "_mp3bot_publication_card", False):
        return

    async def wrapped(self, *args, **kwargs):
        caption = str(kwargs.get("caption") or "")
        if _is_livedub_video_caption(caption):
            source_line = _bold_title_line(caption) or "Переведённое видео"
            card = await build_publication_card(source_line, _CURRENT_SOURCE_URL.get())
            kwargs["caption"] = format_video_caption(card, caption)
            kwargs["parse_mode"] = "HTML"
        return await original(self, *args, **kwargs)

    wrapped._mp3bot_publication_card = True  # type: ignore[attr-defined]
    setattr(cls, "send_video", wrapped)


def _wrap_send_audio(cls: type) -> None:
    original = getattr(cls, "send_audio", None)
    if original is None or getattr(original, "_mp3bot_publication_card", False):
        return

    async def wrapped(self, *args, **kwargs):
        if _is_livedub_audio_caption(kwargs.get("caption")):
            title = _plain(kwargs.get("title"), 220)
            performer = _plain(kwargs.get("performer"), 120)
            source_line = f"{title} - {performer}" if performer else title
            card = await build_publication_card(source_line, _CURRENT_SOURCE_URL.get())
            kwargs["title"] = card.get("title") or title or None
            kwargs["performer"] = card.get("author") or _canonical_author(performer) or None
            public_caption = format_audio_caption(card)
            if public_caption:
                kwargs["caption"] = public_caption
                kwargs["parse_mode"] = "HTML"
            else:
                kwargs.pop("caption", None)
                kwargs.pop("parse_mode", None)
        return await original(self, *args, **kwargs)

    wrapped._mp3bot_publication_card = True  # type: ignore[attr-defined]
    setattr(cls, "send_audio", wrapped)


def _reuse_and_suppress_legacy_info_card() -> None:
    """Avoid a second AI call/message after the inline publication card."""
    from services import livedub_info as module

    original_build = module.build_livedub_info_card
    original_format = module.format_livedub_info_message
    if getattr(original_build, "_mp3bot_inline_publication", False):
        return

    async def build(title_line, dub_srt_path=None, *, source_url="", force=False):
        current_url = _source_url(source_url or _CURRENT_SOURCE_URL.get())
        key = _cache_key(str(title_line or ""), current_url)
        cached = _PUBLICATION_CACHE.get(key)
        if not cached and current_url:
            cached = _PUBLICATION_CACHE.get(current_url.casefold())
        if cached:
            return {
                "telegram_description": cached.get("description", ""),
                "youtube_title": _title_line(cached),
                "youtube_description": cached.get("description", ""),
                "compact_subtitles": [],
                "hashtags": [],
                "key_theological_terms": [],
                "scripture_references": [],
                "source_url": current_url,
                "source": "inline_publication_cache",
            }
        return await original_build(
            title_line,
            dub_srt_path,
            source_url=source_url,
            force=force,
        )

    def format_message(card: dict) -> str:
        # During process_single_video the same information is already attached to
        # the video and MP3. Outside that context preserve the reusable formatter.
        if _CURRENT_SOURCE_URL.get():
            return ""
        return original_format(card)

    build._mp3bot_inline_publication = True  # type: ignore[attr-defined]
    format_message._mp3bot_inline_publication = True  # type: ignore[attr-defined]
    module.build_livedub_info_card = build
    module.format_livedub_info_message = format_message


def install_livedub_publication() -> None:
    """Install after output policy and before the LiveDub audio companion."""
    if not _enabled():
        return
    with _INSTALL_LOCK:
        _install_source_context()
        _reuse_and_suppress_legacy_info_card()
        from telegram import Bot

        _wrap_send_video(Bot)
        _wrap_send_audio(Bot)
        try:
            from telegram.ext import ExtBot

            if ExtBot.send_video is not Bot.send_video:
                _wrap_send_video(ExtBot)
            if ExtBot.send_audio is not Bot.send_audio:
                _wrap_send_audio(ExtBot)
        except Exception as exc:
            logger.debug("[LiveDubPublication] ExtBot patch skipped: %s", exc)
        logger.info("✨ LiveDub publication: Russian title + description + source link enabled")
