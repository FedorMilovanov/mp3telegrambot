#!/usr/bin/env python3
"""Maximum-quality Gemini planning for the standalone Shorts Factory mode."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any

from core.globals import GEMINI_CLIENTS, HAS_GEMINI, make_audio_config
from core.text_utils import normalize_author_name, normalize_title_text, title_case_fragment
from core.utils import format_timestamp

try:
    from google.genai import types
except ImportError:  # pragma: no cover - runtime dependency is checked elsewhere
    types = None

logger = logging.getLogger(__name__)

SHORT_MIN_SEC = 35
PUBLIC_SHORT_MAX_SEC = 180
SHORT_MAX_SEC = 177
LONG_MIN_SEC = 300
PUBLIC_LONG_MAX_SEC = 900
LONG_MAX_SEC = 897
DEFAULT_SHORTS_FACTORY_MODEL = "gemini-3.7-flash"

_FACTORY_CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "start_seconds": {"type": "number"},
        "end_seconds": {"type": "number"},
        "title_ru": {"type": "string"},
        "hook_ru": {"type": "string"},
        "reason_ru": {"type": "string"},
        "kind": {"type": "string"},
        "hashtags": {
            "type": "array",
            "items": {"type": "string"},
        },
        "quality_score": {"type": "number"},
        "boundary_verified": {"type": "boolean"},
    },
    "required": [
        "start_seconds",
        "end_seconds",
        "title_ru",
        "reason_ru",
        "quality_score",
        "boundary_verified",
    ],
}

FACTORY_PLAN_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "metadata": {
            "type": "object",
            "properties": {
                "language": {"type": "string"},
                "format": {"type": "string"},
                "title_ru": {"type": "string"},
                "author_ru": {"type": "string"},
                "analysis_note": {"type": "string"},
            },
            "required": ["language", "format", "title_ru", "author_ru"],
        },
        "shorts_candidates": {
            "type": "array",
            "items": _FACTORY_CANDIDATE_SCHEMA,
        },
        "long_candidates": {
            "type": "array",
            "items": _FACTORY_CANDIDATE_SCHEMA,
        },
    },
    "required": ["metadata", "shorts_candidates", "long_candidates"],
}


def _require_factory_model(model: str, source: str) -> str:
    """Keep Factory on the supported free-tier Gemini 3.7 Flash route."""
    value = str(model or "").strip()
    if value.casefold() != DEFAULT_SHORTS_FACTORY_MODEL:
        raise RuntimeError(
            "SHORTS FACTORY MAX requires gemini-3.7-flash; "
            f"{source}={value!r} is not allowed"
        )
    return DEFAULT_SHORTS_FACTORY_MODEL


def shorts_factory_model() -> str:
    """Use Gemini 3.7 Flash with the Factory's explicit high-thinking config."""
    explicit = os.getenv("SHORTS_FACTORY_MODEL", "").strip()
    if explicit:
        return _require_factory_model(explicit, "SHORTS_FACTORY_MODEL")
    return DEFAULT_SHORTS_FACTORY_MODEL


def _parse_json_payload(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Gemini did not return a JSON object")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("Gemini JSON root is not an object")
    return data


def _seconds(value: Any) -> float:
    """Parse Factory boundaries without throwing away sub-second audit precision."""
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = str(value or "").strip()
        if not text:
            return 0.0
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            seconds = float(text)
        else:
            parts = text.split(":")
            try:
                nums = [float(part) for part in parts]
            except ValueError:
                return 0.0
            if len(nums) == 2:
                seconds = nums[0] * 60.0 + nums[1]
            elif len(nums) == 3:
                seconds = nums[0] * 3600.0 + nums[1] * 60.0 + nums[2]
            else:
                return 0.0
    if not math.isfinite(seconds):
        return 0.0
    return round(max(0.0, seconds), 3)


def _clean_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        tag = re.sub(r"[^0-9A-Za-zА-Яа-яЁё_]", "", str(item or "").lstrip("#"))
        folded = tag.casefold()
        if tag and folded not in seen:
            out.append(tag[:40])
            seen.add(folded)
    return out[:4]


def _normalize_candidate(
    item: Any,
    *,
    duration: int,
    min_sec: int,
    max_sec: int,
    default_kind: str,
    require_verified: bool,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    if require_verified and item.get("boundary_verified") is not True:
        return None

    start = _seconds(item.get("start_seconds", item.get("start")))
    end = _seconds(item.get("end_seconds", item.get("end")))
    if start < 0 or end <= start:
        return None
    if duration > 0 and end > duration:
        return None
    clip_duration = end - start
    if clip_duration < min_sec or clip_duration > max_sec:
        return None

    title = title_case_fragment(
        normalize_title_text(str(item.get("title_ru") or item.get("title") or ""))
    ).strip()
    if not title:
        return None
    hook = str(item.get("hook_ru") or item.get("hook") or "").strip()
    reason = str(item.get("reason_ru") or item.get("reason") or "").strip()
    kind = str(item.get("kind") or default_kind).strip() or default_kind
    score_raw = item.get("quality_score", item.get("score", 0))
    try:
        score = max(0.0, min(float(score_raw), 100.0))
    except (TypeError, ValueError):
        score = 0.0

    return {
        "start": format_timestamp(start),
        "end": format_timestamp(end),
        "title": title[:140],
        "hook": hook[:180],
        "reason": reason[:320],
        "kind": kind[:50],
        "hashtags": _clean_tags(item.get("hashtags")),
        "start_seconds": start,
        "end_seconds": end,
        "duration_seconds": clip_duration,
        "quality_score": score,
        "boundary_verified": True,
    }


def _remove_heavy_overlap(
    candidates: list[dict[str, Any]],
    *,
    max_overlap: float,
) -> list[dict[str, Any]]:
    ranked = sorted(
        candidates,
        key=lambda item: (-float(item.get("quality_score", 0)), item["start_seconds"]),
    )
    accepted: list[dict[str, Any]] = []
    for candidate in ranked:
        start = candidate["start_seconds"]
        end = candidate["end_seconds"]
        length = max(1, end - start)
        rejected = False
        for other in accepted:
            overlap = max(
                0,
                min(end, other["end_seconds"]) - max(start, other["start_seconds"]),
            )
            if overlap / min(length, max(1, other["duration_seconds"])) > max_overlap:
                rejected = True
                break
        if not rejected:
            accepted.append(candidate)
    return sorted(
        accepted,
        key=lambda item: (-float(item.get("quality_score", 0)), item["start_seconds"]),
    )


def validate_factory_plan(
    raw: dict[str, Any],
    duration: int,
    *,
    require_verified: bool = True,
) -> dict[str, Any]:
    """Enforce duration, verified boundaries and overlap contracts."""
    meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    shorts: list[dict[str, Any]] = []
    longs: list[dict[str, Any]] = []

    raw_shorts = raw.get("shorts_candidates")
    if not isinstance(raw_shorts, list):
        raw_shorts = []
    for item in raw_shorts:
        normalized = _normalize_candidate(
            item,
            duration=duration,
            min_sec=SHORT_MIN_SEC,
            max_sec=SHORT_MAX_SEC,
            default_kind="short_highlight",
            require_verified=require_verified,
        )
        if normalized:
            shorts.append(normalized)

    raw_longs = raw.get("long_candidates")
    if not isinstance(raw_longs, list):
        raw_longs = []
    for item in raw_longs:
        normalized = _normalize_candidate(
            item,
            duration=duration,
            min_sec=LONG_MIN_SEC,
            max_sec=LONG_MAX_SEC,
            default_kind="long_highlight",
            require_verified=require_verified,
        )
        if normalized:
            longs.append(normalized)

    shorts = _remove_heavy_overlap(shorts, max_overlap=0.20)[:5]
    longs = _remove_heavy_overlap(longs, max_overlap=0.45)[:3]

    title = title_case_fragment(
        normalize_title_text(str(meta.get("title_ru") or raw.get("title_ru") or ""))
    ).strip()
    author = normalize_author_name(
        str(meta.get("author_ru") or raw.get("author_ru") or "")
    ).strip()
    language = str(meta.get("language") or raw.get("language") or "").strip().lower()
    format_name = (
        str(meta.get("format") or raw.get("format") or "other").strip().lower()
        or "other"
    )

    return {
        "metadata": {
            "title_ru": title,
            "author_ru": author,
            "language": language,
            "format": format_name,
            "analysis_note": str(meta.get("analysis_note") or "").strip()[:700],
        },
        "shorts_candidates": shorts,
        "long_candidates": longs,
    }


def _scout_prompt(title: str, performer: str, duration: int, source_language: str) -> str:
    return f"""
Ты — главный редактор и режиссёр нарезки. Проанализируй ВСЮ аудиодорожку целиком и создай только план сильнейших самостоятельных видеофрагментов.

Это режим SHORTS FACTORY MAX. Нельзя делать конспект, Telegraph-страницы, вопросы, общий пересказ или дешёвую поверхностную выборку. Задача — найти точные монтажные границы и лучшие законченные смысловые фрагменты.

ИСТОЧНИК:
- исходное название: {title}
- предполагаемый автор/канал: {performer}
- длительность: {format_timestamp(duration)} ({duration} секунд)
- metadata language: {source_language or 'unknown'}

НУЖНЫ ДВЕ КАТЕГОРИИ:
1. shorts_candidates: 5–8 сильных самостоятельных фрагментов длиной 35–177 секунд.
2. long_candidates: 2–4 законченных смысловых фрагмента длиной 300–897 секунд.

Почему потолок ниже публичных 180/900 секунд: рендер должен сохранить начало мысли и добавить полный хвост Яндекс LiveDub, не выходя за итоговые 3/15 минут.

КРИТЕРИИ КАЧЕСТВА:
- прослушай весь материал, не выбирай только начало;
- каждый фрагмент начинается до начала полноценной мысли и заканчивается после её естественного завершения;
- не режь вопрос, аргумент, цитату, пример, молитву или вывод посередине;
- предпочитай ясный тезис, сильный поворот, самостоятельный ответ, яркую историю, доказательство или кульминацию;
- убирай приветствия, технические вставки, рекламу, повторы и пустые переходы;
- фрагменты одной категории не должны существенно дублировать друг друга;
- русские title_ru/hook_ru/reason_ru должны быть точными, без кликбейта и без выдуманных утверждений;
- quality_score 0–100 оценивает одновременно смысловую силу, самостоятельность и точность границ;
- start_seconds/end_seconds должны соответствовать реальному аудио, а не приблизительному таймлайну;
- на первом проходе boundary_verified всегда false: финальное подтверждение выполняет отдельный аудитор.

Верни СТРОГО JSON с полями metadata, shorts_candidates и long_candidates. Каждый кандидат содержит start_seconds, end_seconds, title_ru, hook_ru, reason_ru, kind, hashtags, quality_score, boundary_verified.
""".strip()


def _judge_prompt(scout_plan: dict[str, Any], duration: int) -> str:
    plan_json = json.dumps(scout_plan, ensure_ascii=False, separators=(",", ":"))
    return f"""
Ты — второй независимый старший редактор. Повторно прослушай исходную аудиодорожку и не доверяй первому плану автоматически.

Длительность источника: {duration} секунд.
ПЕРВИЧНЫЙ ПЛАН:
{plan_json}

Сделай строгий смысловой отбор:
- удали слабые, повторяющиеся, зависимые от внешнего контекста или фактически неточные фрагменты;
- передвинь границы, если фрагмент не является самостоятельной законченной единицей;
- сохрани максимум 5 Shorts длиной 35–177 секунд;
- сохрани максимум 3 длинных фрагмента длиной 300–897 секунд;
- оставь запас для контекстного начала и полного хвоста Яндекс LiveDub внутри итоговых 180/900 секунд;
- quality_score повышай только за реально сильный материал;
- boundary_verified пока оставь false: третья независимая проверка подтвердит точные монтажные границы;
- не добавляй конспект, вопросы, Telegraph или материалы вне нарезки.

Верни тот же строгий JSON-формат с metadata, shorts_candidates и long_candidates.
""".strip()


def _boundary_prompt(judged_plan: dict[str, Any], duration: int) -> str:
    plan_json = json.dumps(judged_plan, ensure_ascii=False, separators=(",", ":"))
    return f"""
Ты — третий независимый аудитор монтажных границ. Это финальный контроль качества SHORTS FACTORY MAX.

Длительность источника: {duration} секунд.
ОТОБРАННЫЙ ПЛАН:
{plan_json}

Для КАЖДОГО кандидата заново прослушай минимум 12 секунд до start_seconds и 12 секунд после end_seconds, насколько позволяет источник.

Обязательные действия:
- начало должно включать подводку, необходимую для понимания, но не пустой переход;
- конец должен включать завершение фразы, аргумента, примера, молитвы или вывода;
- исправь start_seconds/end_seconds до точной естественной границы;
- удали кандидат, если точную самостоятельную границу найти нельзя;
- удали кандидат, если после исправления он выходит за 35–177 секунд для Shorts или 300–897 секунд для long;
- оставь запас для контекстного начала и полного хвоста Яндекс LiveDub внутри итоговых 180/900 секунд;
- boundary_verified=true ставь ТОЛЬКО после фактической проверки обеих границ по аудио;
- сохрани максимум 5 Shorts и максимум 3 long;
- не делай новый смысловой пересказ и не добавляй новые кандидаты вне отобранного плана.

Верни тот же строгий JSON. В финальном ответе каждый сохранённый кандидат обязан иметь boundary_verified=true.
""".strip()


async def _wait_uploaded_file(client, uploaded):
    started = asyncio.get_running_loop().time()
    current = uploaded
    while str(getattr(current, "state", "")).upper().endswith("PROCESSING"):
        if asyncio.get_running_loop().time() - started > 600:
            raise TimeoutError("Gemini audio file processing exceeded 600 seconds")
        await asyncio.sleep(3)
        current = await client.aio.files.get(name=current.name)
    if str(getattr(current, "state", "")).upper().endswith("FAILED"):
        raise RuntimeError("Gemini audio file processing failed")
    return current


async def _response_text(response) -> str:
    text = str(getattr(response, "text", "") or "")
    if text.strip():
        return text
    parts: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            if not getattr(part, "thought", False):
                parts.append(str(getattr(part, "text", "") or ""))
    return "".join(parts)


async def _run_pass(client, *, model: str, audio_part, prompt: str, max_tokens: int):
    config = make_audio_config(
        max_output_tokens=max_tokens,
        model_name=model,
        thinking_level="high",
        response_mime_type="application/json",
        response_schema=FACTORY_PLAN_RESPONSE_SCHEMA,
    )
    response = await asyncio.wait_for(
        client.aio.models.generate_content(
            model=model,
            contents=[audio_part, prompt],
            config=config,
        ),
        timeout=900,
    )
    return _parse_json_payload(await _response_text(response))


async def create_factory_plan(
    mp3_path: Path,
    *,
    title: str,
    performer: str,
    duration: int,
    source_language: str = "",
) -> dict[str, Any]:
    """Run strict scout, editorial judge and boundary-audit passes."""
    if not HAS_GEMINI or not GEMINI_CLIENTS or types is None:
        raise RuntimeError("Gemini is unavailable; SHORTS FACTORY MAX requires Gemini")
    if not mp3_path.exists() or mp3_path.stat().st_size < 1024:
        raise RuntimeError("Audio file for Shorts Factory is missing or empty")

    model = shorts_factory_model()
    file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
    last_error: Exception | None = None

    for client_index, client in enumerate(GEMINI_CLIENTS, 1):
        uploaded_name = ""
        try:
            if file_size_mb <= 20:
                audio_part = types.Part.from_bytes(
                    data=mp3_path.read_bytes(),
                    mime_type="audio/mpeg",
                )
            else:
                uploaded = await client.aio.files.upload(
                    file=mp3_path,
                    config=types.UploadFileConfig(
                        mime_type="audio/mpeg",
                        display_name=f"Shorts Factory MAX — {performer} — {title}",
                    ),
                )
                uploaded = await _wait_uploaded_file(client, uploaded)
                audio_part = uploaded
                uploaded_name = str(getattr(uploaded, "name", "") or "")

            scout = await _run_pass(
                client,
                model=model,
                audio_part=audio_part,
                prompt=_scout_prompt(title, performer, duration, source_language),
                max_tokens=32000,
            )
            judged = await _run_pass(
                client,
                model=model,
                audio_part=audio_part,
                prompt=_judge_prompt(scout, duration),
                max_tokens=28000,
            )
            audited = await _run_pass(
                client,
                model=model,
                audio_part=audio_part,
                prompt=_boundary_prompt(judged, duration),
                max_tokens=28000,
            )

            plan = validate_factory_plan(audited, duration, require_verified=True)
            if not plan["shorts_candidates"] and not plan["long_candidates"]:
                raise RuntimeError(
                    "Three-pass Gemini review produced no candidates with verified boundaries"
                )
            plan["model"] = model
            plan["thinking_level"] = "high"
            plan["review_passes"] = 3
            plan["strict_quality"] = True
            return plan
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Shorts Factory MAX client %d/%d failed strict review: %s: %s",
                client_index,
                len(GEMINI_CLIENTS),
                type(exc).__name__,
                exc,
            )
        finally:
            if uploaded_name:
                try:
                    await client.aio.files.delete(name=uploaded_name)
                except Exception:
                    pass

    raise RuntimeError(f"All Gemini clients failed strict Shorts Factory review: {last_error}")


def factory_ai_data(plan: dict[str, Any], *, title: str, performer: str) -> dict[str, Any]:
    meta = plan.get("metadata") if isinstance(plan.get("metadata"), dict) else {}
    return {
        "format": str(meta.get("format") or "other"),
        "real_title": str(meta.get("title_ru") or title),
        "real_author": str(meta.get("author_ru") or performer),
        "real_event": "",
        "analysis_summary": str(meta.get("analysis_note") or ""),
        "argument_arc": "",
        "key_categories": [],
        "timestamps": "",
        "questions": [],
    }
