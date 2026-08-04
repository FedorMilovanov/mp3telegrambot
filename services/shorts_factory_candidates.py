#!/usr/bin/env python3
"""Maximum-quality Gemini planning for the standalone Shorts Factory mode."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from core.database import GEMINI_MODEL
from core.globals import GEMINI_CLIENTS, HAS_GEMINI, make_audio_config
from core.text_utils import normalize_author_name, normalize_title_text, title_case_fragment
from core.utils import format_timestamp

try:
    from google.genai import types
except ImportError:  # pragma: no cover - runtime dependency is checked elsewhere
    types = None

logger = logging.getLogger(__name__)

SHORT_MIN_SEC = 35
SHORT_MAX_SEC = 180
LONG_MIN_SEC = 300
LONG_MAX_SEC = 900


def shorts_factory_model() -> str:
    """Use the strongest explicitly configured model, never the Lite route."""
    return (
        os.getenv("SHORTS_FACTORY_MODEL", "").strip()
        or os.getenv("GEMINI_MAX_MODEL", "").strip()
        or GEMINI_MODEL
    )


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


def _seconds(value: Any) -> int:
    if isinstance(value, (int, float)):
        return max(0, int(round(float(value))))
    text = str(value or "").strip()
    if not text:
        return 0
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return max(0, int(round(float(text))))
    parts = text.split(":")
    try:
        nums = [float(part) for part in parts]
    except ValueError:
        return 0
    if len(nums) == 2:
        return max(0, int(round(nums[0] * 60 + nums[1])))
    if len(nums) == 3:
        return max(0, int(round(nums[0] * 3600 + nums[1] * 60 + nums[2])))
    return 0


def _clean_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        tag = re.sub(r"[^0-9A-Za-zА-Яа-яЁё_]", "", str(item or "").lstrip("#"))
        if tag and tag.casefold() not in {existing.casefold() for existing in out}:
            out.append(tag[:40])
    return out[:4]


def _normalize_candidate(
    item: Any,
    *,
    duration: int,
    min_sec: int,
    max_sec: int,
    default_kind: str,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
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
        "boundary_verified": bool(item.get("boundary_verified", False)),
    }


def _remove_heavy_overlap(candidates: list[dict[str, Any]], *, max_overlap: float) -> list[dict[str, Any]]:
    ranked = sorted(candidates, key=lambda item: (-float(item.get("quality_score", 0)), item["start_seconds"]))
    accepted: list[dict[str, Any]] = []
    for candidate in ranked:
        start = candidate["start_seconds"]
        end = candidate["end_seconds"]
        length = max(1, end - start)
        rejected = False
        for other in accepted:
            overlap = max(0, min(end, other["end_seconds"]) - max(start, other["start_seconds"]))
            if overlap / min(length, max(1, other["duration_seconds"])) > max_overlap:
                rejected = True
                break
        if not rejected:
            accepted.append(candidate)
    return sorted(accepted, key=lambda item: (-float(item.get("quality_score", 0)), item["start_seconds"]))


def validate_factory_plan(raw: dict[str, Any], duration: int) -> dict[str, Any]:
    """Deterministically enforce duration, completeness and overlap contracts."""
    meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    shorts: list[dict[str, Any]] = []
    longs: list[dict[str, Any]] = []

    for item in raw.get("shorts_candidates", []) if isinstance(raw.get("shorts_candidates"), list) else []:
        normalized = _normalize_candidate(
            item,
            duration=duration,
            min_sec=SHORT_MIN_SEC,
            max_sec=SHORT_MAX_SEC,
            default_kind="short_highlight",
        )
        if normalized:
            shorts.append(normalized)

    for item in raw.get("long_candidates", []) if isinstance(raw.get("long_candidates"), list) else []:
        normalized = _normalize_candidate(
            item,
            duration=duration,
            min_sec=LONG_MIN_SEC,
            max_sec=LONG_MAX_SEC,
            default_kind="long_highlight",
        )
        if normalized:
            longs.append(normalized)

    shorts = _remove_heavy_overlap(shorts, max_overlap=0.20)[:5]
    longs = _remove_heavy_overlap(longs, max_overlap=0.45)[:3]

    title = title_case_fragment(
        normalize_title_text(str(meta.get("title_ru") or raw.get("title_ru") or ""))
    ).strip()
    author = normalize_author_name(str(meta.get("author_ru") or raw.get("author_ru") or "")).strip()
    language = str(meta.get("language") or raw.get("language") or "").strip().lower()
    format_name = str(meta.get("format") or raw.get("format") or "other").strip().lower() or "other"

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
1. shorts_candidates: 5–8 сильных самостоятельных фрагментов длиной 35–180 секунд.
2. long_candidates: 2–4 законченных смысловых фрагмента длиной 300–900 секунд (5–15 минут).

КРИТЕРИИ КАЧЕСТВА:
- прослушай весь материал, не выбирай только начало;
- каждый фрагмент начинается до начала полноценной мысли и заканчивается после её естественного завершения;
- не режь вопрос, аргумент, цитату, пример, молитву или вывод посередине;
- предпочитай ясный тезис, сильный поворот, самостоятельный ответ, яркую историю, доказательство или кульминацию;
- убирай приветствия, технические вставки, рекламу, повторы и пустые переходы;
- фрагменты одной категории не должны существенно дублировать друг друга;
- русские title_ru/hook_ru/reason_ru должны быть точными, без кликбейта и без выдуманных утверждений;
- quality_score 0–100 оценивает одновременно смысловую силу, самостоятельность и точность границ;
- start_seconds/end_seconds должны соответствовать реальному аудио, а не приблизительному таймлайну.

Верни СТРОГО JSON:
{{
  "metadata": {{
    "language": "ru|en|other",
    "format": "sermon|lecture|interview|qa|discussion|story|other",
    "title_ru": "точное русское название",
    "author_ru": "автор/спикер",
    "analysis_note": "кратко, по какому принципу выбраны фрагменты"
  }},
  "shorts_candidates": [
    {{
      "start_seconds": 0,
      "end_seconds": 0,
      "title_ru": "",
      "hook_ru": "",
      "reason_ru": "",
      "kind": "thesis|answer|story|warning|application|climax|other",
      "hashtags": [""],
      "quality_score": 0,
      "boundary_verified": false
    }}
  ],
  "long_candidates": [
    {{
      "start_seconds": 0,
      "end_seconds": 0,
      "title_ru": "",
      "reason_ru": "",
      "kind": "complete_argument|qa_answer|story|exposition|other",
      "hashtags": [""],
      "quality_score": 0,
      "boundary_verified": false
    }}
  ]
}}
""".strip()


def _judge_prompt(scout_plan: dict[str, Any], duration: int) -> str:
    plan_json = json.dumps(scout_plan, ensure_ascii=False, separators=(",", ":"))
    return f"""
Ты — второй независимый старший редактор и контролёр точности монтажа. Повторно сверяй каждый кандидат с исходной аудиодорожкой, а не доверяй первому плану.

Длительность источника: {duration} секунд.
ПЕРВИЧНЫЙ ПЛАН:
{plan_json}

Сделай строгую финальную ревизию:
- прослушай контекст до и после каждой границы;
- передвинь start/end, если обрезано начало или завершение мысли;
- удали слабые, повторяющиеся, зависимые от внешнего контекста или фактически неточные фрагменты;
- сохрани максимум 5 Shorts длиной 35–180 секунд;
- сохрани максимум 3 длинных фрагмента длиной 300–900 секунд;
- quality_score повышай только за реально сильный и законченный материал;
- boundary_verified=true ставь только после фактической проверки обеих границ по аудио;
- не добавляй конспект, вопросы, Telegraph или материалы вне нарезки.

Верни тот же JSON-формат с metadata, shorts_candidates и long_candidates. Только финальный исправленный план.
""".strip()


async def _wait_uploaded_file(client, uploaded):
    started = asyncio.get_running_loop().time()
    current = uploaded
    while str(getattr(current, "state", "")) == "PROCESSING":
        if asyncio.get_running_loop().time() - started > 600:
            raise TimeoutError("Gemini audio file processing exceeded 600 seconds")
        await asyncio.sleep(3)
        current = await client.aio.files.get(name=current.name)
    if str(getattr(current, "state", "")) == "FAILED":
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


async def create_factory_plan(
    mp3_path: Path,
    *,
    title: str,
    performer: str,
    duration: int,
    source_language: str = "",
) -> dict[str, Any]:
    """Run a two-pass, high-thinking, audio-grounded selection and judge pass."""
    if not HAS_GEMINI or not GEMINI_CLIENTS or types is None:
        raise RuntimeError("Gemini is unavailable; SHORTS FACTORY MAX requires Gemini")
    if not mp3_path.exists() or mp3_path.stat().st_size < 1024:
        raise RuntimeError("Audio file for Shorts Factory is missing or empty")

    model = shorts_factory_model()
    scout_prompt = _scout_prompt(title, performer, duration, source_language)
    file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
    last_error: Exception | None = None

    for client_index, client in enumerate(GEMINI_CLIENTS, 1):
        uploaded_name = ""
        try:
            if file_size_mb <= 20:
                audio_part = types.Part.from_bytes(data=mp3_path.read_bytes(), mime_type="audio/mpeg")
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

            scout_config = make_audio_config(
                max_output_tokens=32000,
                model_name=model,
                thinking_level="high",
                response_mime_type="application/json",
            )
            scout_response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model,
                    contents=[audio_part, scout_prompt],
                    config=scout_config,
                ),
                timeout=900,
            )
            scout = _parse_json_payload(await _response_text(scout_response))

            judge_config = make_audio_config(
                max_output_tokens=26000,
                model_name=model,
                thinking_level="high",
                response_mime_type="application/json",
            )
            try:
                judge_response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=model,
                        contents=[audio_part, _judge_prompt(scout, duration)],
                        config=judge_config,
                    ),
                    timeout=900,
                )
                reviewed = _parse_json_payload(await _response_text(judge_response))
            except Exception as judge_error:
                logger.warning(
                    "Shorts Factory judge pass failed on client %d; using validated scout plan: %s",
                    client_index,
                    judge_error,
                )
                reviewed = scout

            plan = validate_factory_plan(reviewed, duration)
            if not plan["shorts_candidates"] and not plan["long_candidates"]:
                raise RuntimeError("Gemini returned no valid candidates after deterministic validation")
            plan["model"] = model
            plan["thinking_level"] = "high"
            plan["review_passes"] = 2
            return plan
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Shorts Factory MAX client %d/%d failed: %s: %s",
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

    raise RuntimeError(f"All Gemini clients failed in Shorts Factory MAX: {last_error}")


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
