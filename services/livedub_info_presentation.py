#!/usr/bin/env python3
"""Polish LiveDub title/description output for direct Telegram publication.

The base LiveDub info module intentionally exposes a rich copy-ready bundle.
For the bot chat that was too verbose: it produced headings such as
"Готовое описание к переводу", a second YouTube block and several technical
sections.  This adapter keeps the existing generation logic, adds a cheap
second-chance title translation across all configured Gemini clients/models,
and replaces only the final presentation with a concise publication card.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import threading
from typing import Any

logger = logging.getLogger(__name__)

_INSTALL_LOCK = threading.Lock()
_TRUE = {"1", "true", "yes", "on"}

_AUTHOR_OVERRIDES = {
    "r.c. sproul": "Р. Ч. Спроул",
    "r. c. sproul": "Р. Ч. Спроул",
    "rc sproul": "Р. Ч. Спроул",
    "john macarthur": "Джон МакАртур",
    "paul washer": "Пол Вошер",
    "nathan busenitz": "Натан Бузениц",
    "theodore cabal": "Теодор Кабал",
    "dr. theodore cabal": "Теодор Кабал",
    "doctor theodore cabal": "Теодор Кабал",
}


def _enabled() -> bool:
    return os.getenv("LIVEDUB_CLEAN_INFO_CARD", "1").strip().lower() in _TRUE


def _clean(value: Any, limit: int = 1000) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n-—–|;:")
    return text[:limit].strip()


def _canonical_author(value: str) -> str:
    text = _clean(value, 120)
    low = text.lower()
    if low in _AUTHOR_OVERRIDES:
        return _AUTHOR_OVERRIDES[low]
    try:
        from core.person_names import canonical_person_name, known_author_from_text

        known = known_author_from_text(text)
        return canonical_person_name(known or text)
    except Exception:
        return text


def _looks_like_author(value: str) -> bool:
    text = _clean(value, 120)
    if not text:
        return False
    if text.lower() in _AUTHOR_OVERRIDES:
        return True
    try:
        from core.person_names import known_author_from_text, known_ru_author_from_text, looks_like_author_list

        return bool(
            known_author_from_text(text)
            or known_ru_author_from_text(text)
            or looks_like_author_list(text)
        )
    except Exception:
        words = text.split()
        return 2 <= len(words) <= 6 and all(w[:1].isupper() for w in words if w)


def _split_title_author(line: str) -> tuple[str, str]:
    text = _clean(line, 260)
    if not text:
        return "", ""
    for separator in (" - ", " — ", " – ", " | "):
        if separator not in text:
            continue
        left, right = [part.strip() for part in text.rsplit(separator, 1)]
        if left and right and _looks_like_author(right):
            return left, _canonical_author(right)
        if left and right and _looks_like_author(left):
            return right, _canonical_author(left)
    return text, ""


def _title_has_cyrillic(line: str) -> bool:
    title, _author = _split_title_author(line)
    return bool(re.search(r"[А-Яа-яЁё]", title))


def _fallback_description(title: str, author: str) -> str:
    title = _clean(title, 220)
    author = _canonical_author(author)
    if not title:
        return "Русская аудиоверсия переведённого видео."
    subject = author or "Автор"
    if title.endswith("?"):
        return f"{subject} разбирает вопрос: «{title}»"
    return f"{subject} рассматривает тему «{title}»."


def _strip_json_fence(text: str) -> str:
    text = str(text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


async def _translate_title_second_chance(title_line: str) -> tuple[str, str] | None:
    """Try every configured light/main model and every Gemini client.

    The original implementation iterates models but always uses client 0.  A
    second configured key/client can therefore never rescue a regional or
    transient failure.  This call is deliberately tiny and faithful: translate
    metadata only, without inventing a new headline.
    """
    if not title_line or _title_has_cyrillic(title_line):
        return None
    try:
        from core.globals import GEMINI_CLIENTS, make_text_config_smart
        from services.livedub_info import get_light_model, get_light_model_fallbacks
    except Exception:
        return None
    if not GEMINI_CLIENTS:
        return None

    models: list[str] = []
    for model in [get_light_model(), *get_light_model_fallbacks()]:
        model = str(model or "").strip()
        if model and model not in models:
            models.append(model)

    prompt = f"""
Переведи исходное название христианского видео на русский язык.
Верни строго JSON: {{"title":"...","author":"..."}}.

Правила:
- переводи максимально близко к оригиналу, без нового кликбейта и без отсебятины;
- сохраняй номера частей, серий, главы и стихи Писания;
- если имя автора есть, вынеси его в author и используй принятое русское написание;
- не добавляй автора, которого нельзя установить из строки;
- title не должен содержать автора в конце;
- не добавляй кавычки, слова «перевод», «видео», «проповедь» или пояснения;
- R.C. Sproul = Р. Ч. Спроул; John MacArthur = Джон МакАртур;
  Nathan Busenitz = Натан Бузениц; Theodore Cabal = Теодор Кабал.

Исходная строка: {title_line}
""".strip()

    for model in models:
        for client_index, client in enumerate(GEMINI_CLIENTS):
            try:
                cfg = make_text_config_smart(
                    temperature=0.0,
                    max_output_tokens=300,
                    model_name=model,
                    thinking_level="minimal",
                    response_mime_type="application/json",
                    response_schema={
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "author": {"type": "string"},
                        },
                        "required": ["title", "author"],
                    },
                )
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=cfg,
                    ),
                    timeout=30.0,
                )
                data = json.loads(_strip_json_fence(getattr(response, "text", "") or ""))
                title = _clean(data.get("title"), 190)
                author = _canonical_author(_clean(data.get("author"), 100))
                if title and re.search(r"[А-Яа-яЁё]", title):
                    return title, author
            except Exception as exc:
                logger.info(
                    "[LiveDubInfoPresentation] title model=%s client=%d failed: %s",
                    model,
                    client_index,
                    str(exc)[:120],
                )
    return None


def _make_formatter(module: Any):
    safe_trim_caption = module.safe_trim_caption

    def format_message(card: dict) -> str:
        if not isinstance(card, dict):
            return ""
        raw_line = _clean(card.get("youtube_title") or card.get("telegram_description"), 260)
        title, author = _split_title_author(raw_line)
        description = _clean(card.get("telegram_description"), 700)
        source_url = _clean(card.get("source_url"), 500)
        hashtags = [
            _clean(tag, 80)
            for tag in (card.get("hashtags") or [])[:6]
            if _clean(tag, 80)
        ]

        if not title:
            title = "Переведённое видео"
        if not description or description.casefold() in {
            raw_line.casefold(),
            title.casefold(),
        }:
            description = _fallback_description(title, author)

        lines = [f"<b>{html.escape(title, quote=False)}</b>"]
        if author:
            lines.append(f"👤 {html.escape(author, quote=False)}")
        if description:
            lines += ["", html.escape(description, quote=False)]
        if source_url:
            lines += [
                "",
                f"🔗 <a href=\"{html.escape(source_url, quote=True)}\">Оригинал на YouTube</a>",
            ]
        if hashtags:
            lines += ["", html.escape(" ".join(hashtags), quote=False)]
        return safe_trim_caption("\n".join(lines).strip(), 3900)

    return format_message


def install_livedub_info_presentation() -> None:
    if not _enabled():
        return
    with _INSTALL_LOCK:
        import services.livedub_info as module

        original_build = module.build_livedub_info_card
        if getattr(original_build, "_mp3bot_clean_presentation", False):
            return

        async def build_card(title_line, dub_srt_path=None, *, source_url="", force=False):
            card = await original_build(
                title_line,
                dub_srt_path,
                source_url=source_url,
                force=force,
            )
            card = dict(card or {})
            current_line = _clean(card.get("youtube_title") or title_line, 260)
            translated = None
            if current_line and not _title_has_cyrillic(current_line):
                translated = await _translate_title_second_chance(current_line)
            if translated:
                translated_title, translated_author = translated
                card["youtube_title"] = (
                    f"{translated_title} - {translated_author}"
                    if translated_author else translated_title
                )
            elif not card.get("youtube_title"):
                card["youtube_title"] = _clean(title_line, 220)

            final_title, final_author = _split_title_author(
                _clean(card.get("youtube_title") or title_line, 260)
            )
            description = _clean(card.get("telegram_description"), 700)
            if (
                not description
                or description.casefold() in {
                    _clean(title_line, 260).casefold(),
                    final_title.casefold(),
                }
                or not re.search(r"[А-Яа-яЁё]", description)
            ):
                card["telegram_description"] = _fallback_description(
                    final_title,
                    final_author,
                )
            if source_url:
                card["source_url"] = source_url
            return card

        build_card._mp3bot_clean_presentation = True  # type: ignore[attr-defined]
        if getattr(original_build, "_mp3bot_all_clients", False):
            build_card._mp3bot_all_clients = True  # type: ignore[attr-defined]
        module.build_livedub_info_card = build_card
        module.format_livedub_info_message = _make_formatter(module)
        logger.info(
            "✨ LiveDub info presentation: ✅ faithful RU title + concise publication card"
        )
