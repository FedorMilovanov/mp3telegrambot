#!/usr/bin/env python3
"""Bounded, genuinely light publication-card builder for LiveDub."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)
_TRUE = {"1", "true", "yes", "on"}
_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_INFLIGHT: dict[str, asyncio.Task] = {}

_RU_LOWER_WORDS = {
    "а", "без", "близ", "бы", "в", "вместо", "вне", "во", "вокруг", "для",
    "до", "за", "из", "из-за", "или", "и", "к", "ко", "кроме", "ли", "между",
    "на", "над", "не", "ни", "но", "о", "об", "около", "от", "перед", "по",
    "под", "после", "при", "про", "против", "ради", "с", "среди", "со", "у",
    "через", "же", "да",
}
_TITLE_OVERRIDES = {
    "the battle against sexual immorality & pornography":
        "Борьба с Сексуальной Безнравственностью и Порнографией",
}
_KNOWN_ACRONYMS = {"ИИ", "ВЗ", "НЗ", "США", "РФ", "СССР", "РПЦ"}
_AUTHOR_OVERRIDES = {
    "tim conway": "Тим Конвей",
    "r.c. sproul": "Р. Ч. Спроул",
    "r. c. sproul": "Р. Ч. Спроул",
    "rc sproul": "Р. Ч. Спроул",
    "john macarthur": "Джон МакАртур",
    "paul washer": "Пол Вошер",
    "abner chou": "Абнер Чау",
    "costi hinn": "Кости Хинн",
}


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip() or str(default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high))


def plain(value: Any, limit: int = 1000) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip(" \t\r\n-—–|;:")[:limit].strip()


def _line_key(value: str) -> str:
    return "line:" + plain(value, 320).casefold()


def _url_key(value: str) -> str:
    value = plain(value, 600)
    return "url:" + value.casefold() if re.match(r"^https?://", value, re.I) else ""


def _cache_limit() -> int:
    return _env_int("LIVEDUB_PUBLICATION_CACHE_MAX", 256, 16, 4096)


def cache_get(*keys: str) -> dict[str, Any] | None:
    for key in keys:
        if not key or key not in _CACHE:
            continue
        card = _CACHE.pop(key)
        _CACHE[key] = card
        return dict(card)
    return None


def cache_put(card: dict[str, Any], *keys: str) -> None:
    for key in dict.fromkeys(key for key in keys if key):
        _CACHE.pop(key, None)
        _CACHE[key] = dict(card)
    while len(_CACHE) > _cache_limit():
        _CACHE.popitem(last=False)


def canonical_author(value: str) -> str:
    text = plain(value, 120)
    if not text:
        return ""
    override = _AUTHOR_OVERRIDES.get(text.casefold())
    if override:
        return override
    try:
        from core.person_names import canonical_person_name, known_author_from_text

        normalized = canonical_person_name(known_author_from_text(text) or text)
        return _AUTHOR_OVERRIDES.get(normalized.casefold(), normalized)
    except Exception:
        return text


def split_title_author(value: str) -> tuple[str, str]:
    text = plain(value, 340)
    try:
        from services.livedub_output_policy import _split_title_author

        title, author = _split_title_author(text)
        return plain(title, 240), canonical_author(author)
    except Exception:
        pass
    for separator in (" - ", " — ", " – ", " | "):
        if separator in text:
            title, author = text.rsplit(separator, 1)
            return plain(title, 240), canonical_author(author)
    return text, ""


def _capitalize_word(word: str) -> str:
    if not word:
        return word
    if word in _KNOWN_ACRONYMS or re.search(r"[а-яё][А-ЯЁ]", word):
        return word
    if word.isupper() and len(word) > 1:
        word = word.lower()
    for index, char in enumerate(word):
        if char.isalpha():
            return word[:index] + char.upper() + word[index + 1:]
    return word


def russian_title_case(value: str) -> str:
    text = re.sub(r"\s+", " ", plain(value, 240)).strip()
    words = text.split(" ") if text else []
    out: list[str] = []
    for index, token in enumerate(words):
        match = re.match(
            r"^([^A-Za-zА-Яа-яЁё0-9]*)(.*?)([^A-Za-zА-Яа-яЁё0-9]*)$",
            token,
        )
        if not match:
            out.append(token)
            continue
        prefix, core, suffix = match.groups()
        if 0 < index < len(words) - 1 and core.casefold() in _RU_LOWER_WORDS:
            core = core.lower()
        else:
            core = _capitalize_word(core)
        out.append(prefix + core + suffix)
    return " ".join(out)


def canonical_title(value: str) -> str:
    title = plain(value, 240)
    if not title:
        return ""
    if title.casefold() in _TITLE_OVERRIDES:
        return _TITLE_OVERRIDES[title.casefold()]
    try:
        from core.text_utils import normalize_title_text

        title = normalize_title_text(title) or title
    except Exception:
        pass
    return russian_title_case(title) if re.search(r"[А-Яа-яЁё]", title) else title


def metadata_text(value: str, limit: int = 64) -> str:
    text = plain(value, max(1, limit * 3))
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    if " " in cut:
        candidate = cut.rsplit(" ", 1)[0].rstrip(" -—–,.;:")
        if len(candidate) >= max(12, limit // 2):
            cut = candidate
    return cut.rstrip(" -—–,.;:")


def safe_audio_filename(value: str) -> str:
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", plain(value, 180))
    stem = re.sub(r"\s+", " ", stem).strip(" ._-")[:120]
    return f"{stem or 'Переведённый материал'}.mp3"


def publication_models() -> list[str]:
    configured = os.getenv("GEMINI_LIGHT_MODEL", "gemini-3.5-flash-lite").strip()
    raw = os.getenv("LIVEDUB_PUBLICATION_FALLBACK_MODELS", "").strip()
    models = [configured or "gemini-3.5-flash-lite"]
    models.extend(item.strip() for item in raw.split(",") if item.strip())
    allow_strong = (
        os.getenv("LIVEDUB_PUBLICATION_ALLOW_STRONG_FALLBACK", "0").strip().lower()
        in _TRUE
    )
    out: list[str] = []
    for model in models:
        if not allow_strong and "lite" not in model.casefold():
            continue
        if model and model not in out:
            out.append(model)
    return out or ["gemini-3.5-flash-lite"]


def _economy_config(model_name: str):
    """Bypass the global high-thinking hook for this mechanical text task."""
    try:
        from core.globals import _build_thinking_config, types

        if types is None:
            return None
        kwargs: dict[str, Any] = {
            "max_output_tokens": 700,
            "response_mime_type": "application/json",
            "response_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "author": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["title", "author", "description"],
            },
        }
        if str(model_name).casefold().startswith("gemini-3"):
            thinking = _build_thinking_config("minimal")
            if thinking is not None:
                kwargs["thinking_config"] = thinking
        else:
            kwargs["temperature"] = 0.1
        return types.GenerateContentConfig(**kwargs)
    except Exception:
        return None


def clean_description(value: Any) -> str:
    text = plain(value, 420)
    text = re.sub(r"^[🎙️🎧📖💬✨🔥]+\s*", "", text).strip()
    text = re.sub(
        r"(?i)\b(?:живые голоса яндекса|русская аудиоверсия|перевод яндекса|"
        r"искусственный интеллект|ии)\b[.:—–-]*\s*",
        "",
        text,
    ).strip()
    return text


def fallback_description(title: str, author: str) -> str:
    title, author = canonical_title(title), canonical_author(author)
    if title and title != "Переведённый Материал":
        if author:
            return f"{author} рассматривает тему «{title}» и раскрывает её основные стороны."
        return f"Материал посвящён теме «{title}» и её основным сторонам."
    if author:
        return f"Материал {author}; оригинальная публикация доступна по ссылке."
    return "Оригинальная публикация доступна по ссылке."


async def _generate_light(source_line: str) -> dict[str, str] | None:
    try:
        from core.globals import GEMINI_CLIENTS
    except Exception:
        return None
    if not GEMINI_CLIENTS:
        return None
    source = plain(source_line, 340)
    if not source:
        return None
    prompt = f"""
Подготовь одну компактную карточку для русскоязычного Telegram.
Исходная строка содержит название христианской проповеди/лекции и, возможно, автора.
Верни строго JSON: {{"title":"...","author":"...","description":"..."}}.

Правила:
- title: точный естественный перевод на русский, без кликбейта и отсебятины;
- значимые слова title с заглавной, но предлоги, союзы и частицы внутри — со строчной;
- author: принятое русское написание имени, отдельно от title;
- description: 1–2 живых предложения, 120–300 знаков, только то, что надёжно следует
  из названия; не придумывай тезисы, цитаты, места Писания, события или выводы;
- не пиши «в этом видео», «русская аудиоверсия», «перевод Яндекса», «ИИ»;
- не добавляй эмодзи, ссылку, хэштеги и технические пояснения;
- Tim Conway = Тим Конвей; R.C. Sproul = Р. Ч. Спроул;
  John MacArthur = Джон МакАртур; Paul Washer = Пол Вошер.

Исходная строка: {source}
""".strip()
    attempts = _env_int("LIVEDUB_PUBLICATION_MAX_ATTEMPTS", 2, 1, 8)
    per_timeout = _env_int("LIVEDUB_PUBLICATION_ATTEMPT_TIMEOUT_SEC", 14, 5, 45)
    total_timeout = _env_int("LIVEDUB_PUBLICATION_TOTAL_TIMEOUT_SEC", 28, 8, 90)
    deadline = asyncio.get_running_loop().time() + total_timeout
    used = 0
    for model in publication_models():
        for client_index, client in enumerate(list(GEMINI_CLIENTS)):
            if used >= attempts:
                return None
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining < 2:
                return None
            used += 1
            try:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=_economy_config(model),
                    ),
                    timeout=min(float(per_timeout), remaining),
                )
                raw = str(getattr(response, "text", "") or "").strip()
                raw = re.sub(
                    r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE
                ).strip()
                data = json.loads(raw)
                title = canonical_title(str(data.get("title") or ""))
                author = canonical_author(str(data.get("author") or ""))
                description = clean_description(data.get("description"))
                if title and re.search(r"[А-Яа-яЁё]", title) and description:
                    return {
                        "title": title,
                        "author": author,
                        "description": description,
                        "model": model,
                    }
            except Exception as exc:
                logger.info(
                    "[LiveDubPublicationCore] model=%s client=%d failed: %s",
                    model,
                    client_index,
                    str(exc)[:140],
                )
    return None


async def _build_uncached(source_line: str, source_url: str) -> dict[str, Any]:
    raw_title, raw_author = split_title_author(source_line)
    generated = await _generate_light(source_line)
    if generated:
        title = generated["title"]
        author = generated.get("author") or raw_author
        description = generated["description"]
        model = generated.get("model", "")
    else:
        title = canonical_title(raw_title)
        if not re.search(r"[А-Яа-яЁё]", title):
            title = _TITLE_OVERRIDES.get(raw_title.casefold(), "Переведённый Материал")
        author = canonical_author(raw_author)
        description = fallback_description(title, author)
        model = "deterministic_fallback"
    return {
        "title": canonical_title(title) or "Переведённый Материал",
        "author": canonical_author(author),
        "description": clean_description(description) or fallback_description(title, author),
        "source_url": source_url,
        "model": model,
        "source": "deep_publication",
    }


async def build_publication_card(source_line: str, source_url: str = "") -> dict[str, Any]:
    try:
        import services.livedub_publication as publication

        source_url = publication._source_url(
            source_url or publication._CURRENT_SOURCE_URL.get()
        )
    except Exception:
        source_url = plain(source_url, 600)
    url_key = _url_key(source_url)
    primary_key = url_key or _line_key(source_line)
    # A title can legitimately exist at several URLs. Never borrow a card by
    # title when a concrete source URL is known, or the link may be wrong.
    cached = cache_get(primary_key)
    if cached:
        return cached

    task = _INFLIGHT.get(primary_key)
    if task is None or task.done():
        task = asyncio.create_task(_build_uncached(source_line, source_url))
        _INFLIGHT[primary_key] = task

        def drop_finished(done: asyncio.Task, key: str = primary_key) -> None:
            if _INFLIGHT.get(key) is done:
                _INFLIGHT.pop(key, None)

        task.add_done_callback(drop_finished)
    card = dict(await asyncio.shield(task))
    title_line = str(card.get("title") or "")
    if card.get("author"):
        title_line += f" - {card['author']}"
    aliases = [primary_key]
    if not url_key:
        aliases.extend((_line_key(source_line), _line_key(title_line)))
    cache_put(card, *aliases)
    return card


def compatible_info_card(card: dict[str, Any]) -> dict[str, Any]:
    title, author = str(card.get("title") or ""), str(card.get("author") or "")
    title_line = f"{title} - {author}" if title and author else title or author
    return {
        "telegram_description": card.get("description", ""),
        "youtube_title": title_line,
        "youtube_description": card.get("description", ""),
        "compact_subtitles": [],
        "hashtags": [],
        "key_theological_terms": [],
        "scripture_references": [],
        "source_url": card.get("source_url", ""),
        "source": "deep_publication",
        "model": card.get("model", ""),
    }
