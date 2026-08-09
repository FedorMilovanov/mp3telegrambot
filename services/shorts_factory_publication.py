#!/usr/bin/env python3
"""Cheap, optional publication polish for SHORTS FACTORY captions."""
from __future__ import annotations

import asyncio
import copy
import html
import json
import logging
import os
from typing import Any, Callable

from core.text_utils import normalize_hashtag

logger = logging.getLogger(__name__)
_DESCRIPTION_FIELD = "publication_description"
_INSTALLED = False

# Publication prose is deliberately isolated from both the Factory's heavy
# gemini-3.6-flash analysis route and the generic LiveDub model-fallback chain.
# These are the only two stable light text models accepted by this cosmetic pass,
# in cheapest-first order. Do not broaden this to a family-prefix match: a new
# 3.5 model ID must be reviewed explicitly before it can consume publication quota.
FACTORY_PUBLICATION_LIGHT_MODELS: tuple[str, str] = (
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
)


def canonical_public_hashtags(tags: Any, limit: int = 4) -> list[str]:
    """Apply the repository hashtag rule and keep one leading #."""
    if not isinstance(tags, (list, tuple)):
        return []
    out: list[str] = []
    for raw in tags:
        tag = normalize_hashtag(str(raw or ""))
        if tag and tag not in out:
            out.append(tag)
        if len(out) >= limit:
            break
    return out


def _light_models() -> list[str]:
    """Return the fixed cheapest-first model route for optional caption prose."""
    return list(FACTORY_PUBLICATION_LIGHT_MODELS)


def _enabled() -> bool:
    return os.getenv("SHORTS_FACTORY_PUBLICATION_DESCRIPTION", "1").strip().lower() not in {
        "0", "false", "no", "off"
    }


def _timeout() -> float:
    try:
        value = float(os.getenv("SHORTS_FACTORY_PUBLICATION_DESCRIPTION_TIMEOUT_SEC", "12") or 12)
    except (TypeError, ValueError):
        value = 12.0
    return max(5.0, min(value, 25.0))


def _clean_description(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = " ".join(text.split()).strip(" •·—–-*\t\r\n")
    low = text.casefold()
    if not text or any(x in low for x in (
        "gemini", "искусственный интеллект", "нейросет", "сгенерирован",
        "перевод яндекса", "живые голоса яндекса", "в этом видео", "в этом ролике",
    )):
        return ""
    if "<" in text or ">" in text:
        return ""
    text = text[:320].rstrip(" ,;:-—–")
    if len(text) < 55:
        return ""
    return text if text[-1:] in ".!?…" else text + "."


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {
            "type": "object",
            "properties": {"index": {"type": "integer"}, "description": {"type": "string"}},
            "required": ["index", "description"],
        }}},
        "required": ["items"],
    }


def _metadata(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[dict, str, str]:
    ai_data = kwargs.get("ai_data", args[1] if len(args) > 1 else {})
    title = kwargs.get("title", args[2] if len(args) > 2 else "")
    performer = kwargs.get("performer", args[3] if len(args) > 3 else "")
    return ai_data if isinstance(ai_data, dict) else {}, str(title or ""), str(performer or "")


def _strip_json_fence(value: Any) -> str:
    raw = str(value or "").strip()
    if raw.startswith("```"):
        _first, sep, rest = raw.partition("\n")
        raw = rest if sep else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3].rstrip()
    return raw.strip()


async def _generate_descriptions(
    candidates: list[dict[str, Any]], *, args: tuple[Any, ...], kwargs: dict[str, Any], kind: str
) -> dict[int, str]:
    """One batched light request; soft failure means no description, never no video."""
    if not candidates or not _enabled():
        return {}
    try:
        from core.globals import GEMINI_CLIENTS, make_text_config_smart
    except Exception:
        return {}
    if not GEMINI_CLIENTS:
        return {}

    ai_data, source_title, performer = _metadata(args, kwargs)
    author = str(ai_data.get("real_author") or performer or "")[:160]
    event = str(ai_data.get("real_event") or "")[:180]
    fragments = [{
        "index": i,
        "title": str(c.get("title") or "")[:160],
        "hook": str(c.get("hook") or "")[:180],
        "reason": str(c.get("reason") or "")[:240],
    } for i, c in enumerate(candidates)]
    prompt = (
        "Для каждого фрагмента напиши один небольшой естественный абзац для Telegram: "
        "1–2 предложения, примерно 90–240 знаков. Пиши спокойно и по-человечески, "
        "без канцелярита, рекламы и ощущения текста от ИИ. Начинай сразу со смысла, "
        "проблемы или контраста фрагмента; не открывай абзац мета-фразой о ролике и "
        "не описывай действия автора или спикера. Не повторяй заголовок дословно. "
        "Не называй фрагмент сильным, важным, вирусным или цепляющим. Используй только "
        "факты из title/hook/reason; не придумывай цитаты, места Писания или события. "
        "Без эмодзи, ссылок и хэштегов. Верни JSON по схеме.\n"
        f"Тип: {kind}; материал: {source_title[:220]}; автор: {author}; событие: {event}.\n"
        f"Фрагменты: {json.dumps(fragments, ensure_ascii=False)}"
    )

    models = _light_models()
    clients = list(GEMINI_CLIENTS)
    attempts: list[tuple[str, Any]] = [(models[0], clients[0])]
    if len(models) > 1:
        attempts.append((models[1], clients[1] if len(clients) > 1 else clients[0]))
    elif len(clients) > 1:
        attempts.append((models[0], clients[1]))

    for number, (model, client) in enumerate(attempts, 1):
        try:
            cfg = make_text_config_smart(
                temperature=0.25, max_output_tokens=1600, model_name=model,
                thinking_level="minimal", response_mime_type="application/json",
                response_schema=_schema(),
            )
            response = await asyncio.wait_for(
                client.aio.models.generate_content(model=model, contents=prompt, config=cfg),
                timeout=_timeout(),
            )
            data = json.loads(_strip_json_fence(getattr(response, "text", "")))
            out: dict[int, str] = {}
            for item in data.get("items", []) if isinstance(data, dict) else []:
                if not isinstance(item, dict):
                    continue
                try:
                    index = int(item.get("index"))
                except (TypeError, ValueError):
                    continue
                description = _clean_description(item.get("description"))
                if 0 <= index < len(candidates) and description:
                    out[index] = description
            if out:
                logger.info("Factory publication descriptions: %d/%d via %s/minimal", len(out), len(candidates), model)
                return out
        except Exception as exc:
            logger.info("Factory publication description soft-fail %d model=%s: %s", number, model, str(exc)[:140])
    return {}


async def enrich_factory_candidates(
    candidates: list[dict[str, Any]], *, call_args: tuple[Any, ...] = (),
    call_kwargs: dict[str, Any] | None = None, kind: str = "short",
) -> list[dict[str, Any]]:
    enriched = copy.deepcopy(candidates or [])
    for item in enriched:
        item["hashtags"] = canonical_public_hashtags(item.get("hashtags"))
    descriptions = await _generate_descriptions(
        enriched, args=tuple(call_args), kwargs=dict(call_kwargs or {}), kind=kind
    )
    for index, text in descriptions.items():
        enriched[index][_DESCRIPTION_FIELD] = text
    return enriched


def _insert_description(caption: str, description: Any) -> str:
    text = _clean_description(description)
    if not text:
        return str(caption or "")
    parts = [p for p in str(caption or "").split("\n\n") if p]
    escaped = html.escape(text, quote=False)
    return "\n\n".join([parts[0], escaped, *parts[1:]]) if parts else escaped


def wrap_factory_caption_builder(builder: Callable[..., str]) -> Callable[..., str]:
    if getattr(builder, "_factory_publication_polish", False):
        return builder

    def wrapped(*args, **kwargs):
        call_args, call_kwargs = list(args), dict(kwargs)
        if "candidate" in call_kwargs:
            candidate = copy.deepcopy(call_kwargs.get("candidate") or {})
            call_kwargs["candidate"] = candidate
        else:
            candidate = copy.deepcopy(call_args[0] if call_args else {})
            if call_args:
                call_args[0] = candidate
            else:
                call_kwargs["candidate"] = candidate
        candidate["hashtags"] = canonical_public_hashtags(candidate.get("hashtags"))
        caption = builder(*call_args, **call_kwargs)
        return _insert_description(caption, candidate.get(_DESCRIPTION_FIELD))

    wrapped._factory_publication_polish = True  # type: ignore[attr-defined]
    return wrapped


def install_factory_publication_formatters(shorts_module, clips_module) -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True
    shorts_module.build_short_caption = wrap_factory_caption_builder(shorts_module.build_short_caption)
    clips_module.build_clip_caption = wrap_factory_caption_builder(clips_module.build_clip_caption)
    _INSTALLED = True
    logger.info("Factory publication polish: canonical hashtags + optional Gemini 3.5/minimal descriptions")
    return True


__all__ = [
    "FACTORY_PUBLICATION_LIGHT_MODELS",
    "canonical_public_hashtags",
    "enrich_factory_candidates",
    "install_factory_publication_formatters",
    "wrap_factory_caption_builder",
]
