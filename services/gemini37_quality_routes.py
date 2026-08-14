#!/usr/bin/env python3
"""Install Gemini 3.7 quality-first routes without semantic downgrades.

Gemini 3.7 Flash/high is the primary semantic route. Gemini 3.6 Flash/high is
the only quality-preserving fallback. 3.5/Lite never enters Factory candidate
selection or full-sermon translation review.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PRIMARY_MODEL = "gemini-3.7-flash"
FALLBACK_MODEL = "gemini-3.6-flash"
_ALLOWED_QUALITY_MODELS = {PRIMARY_MODEL, FALLBACK_MODEL}
_INSTALLED = False


def quality_model_chain() -> tuple[str, ...]:
    """Return the fail-closed semantic model chain configured for production."""
    primary = os.getenv("GEMINI_MODEL", PRIMARY_MODEL).strip() or PRIMARY_MODEL
    if primary not in _ALLOWED_QUALITY_MODELS:
        raise RuntimeError(
            "Quality-sensitive Gemini work requires gemini-3.7-flash or "
            f"gemini-3.6-flash; GEMINI_MODEL={primary!r} is not allowed"
        )
    raw_fallbacks = os.getenv("GEMINI_QUALITY_FALLBACK_MODELS", FALLBACK_MODEL)
    models = [primary]
    for value in str(raw_fallbacks or "").split(","):
        model = value.strip()
        if not model or model in models:
            continue
        if model not in _ALLOWED_QUALITY_MODELS:
            raise RuntimeError(
                "Semantic fallback may only use gemini-3.7-flash/high or "
                f"gemini-3.6-flash/high; got {model!r}"
            )
        models.append(model)
    return tuple(models)


def _install_factory_model_policy() -> None:
    import services.shorts_factory_candidates as candidates

    def require_factory_model(model: str, source: str) -> str:
        value = str(model or "").strip()
        if value not in _ALLOWED_QUALITY_MODELS:
            raise RuntimeError(
                "SHORTS FACTORY MAX requires Gemini 3.7 Flash/high with "
                "Gemini 3.6 Flash/high as the only semantic fallback; "
                f"{source}={value!r} is not allowed"
            )
        return value

    def factory_model() -> str:
        explicit = os.getenv("SHORTS_FACTORY_MODEL", "").strip()
        return require_factory_model(
            explicit or PRIMARY_MODEL,
            "SHORTS_FACTORY_MODEL" if explicit else "default",
        )

    candidates.DEFAULT_SHORTS_FACTORY_MODEL = PRIMARY_MODEL
    candidates._require_factory_model = require_factory_model
    candidates.shorts_factory_model = factory_model


def _editorial_prompt(editorial: Any, pack_path: Path) -> tuple[dict[str, Any], str]:
    manifest = editorial.load_pack_manifest(pack_path)
    model_manifest = editorial._manifest_for_model(manifest)
    original = editorial._read_pack_text(pack_path, "original.srt")
    russian = editorial._read_pack_text(pack_path, "russian_whisper.srt")
    candidates = editorial._read_pack_text(pack_path, "candidates.json")
    prompt = (
        "Ты редактор переведённой проповеди. Сравни исходный SRT с фактически услышанной "
        "русской речью из Whisper SRT. Проверяй смысл, а не литературную красоту. "
        "Небольшая неестественность русского допустима, если смысл сохранён. Особое внимание "
        "к отрицаниям, субъекту и объекту действия, причинно-следственным связям, именам, "
        "числам, местам Писания и богословским терминам. Не придумывай ошибок, которых нет "
        "в русской Whisper-стенограмме. В manifest.timeline явно описаны разные временные "
        "шкалы: не сопоставляй реплики по одинаковому номеру cue или одинаковой секунде; "
        "сопоставляй по смысловой последовательности и указанной задержке. Таймкоды issue "
        "всегда бери из Russian Whisper / translated-video timeline. drop_span выбирай только "
        "когда удаление короткого дефекта оставляет исходную мысль целой; mute_span только "
        "для чисто звукового дефекта; иначе reject_region. Для каждого candidate_id обязательно "
        "верни ровно одну оценку пригодности. Верни только JSON по схеме.\n\n"
        f"MANIFEST:\n{json.dumps(model_manifest, ensure_ascii=False)}\n\n"
        f"CANDIDATES:\n{candidates}\n\n"
        f"ORIGINAL SRT:\n{original}\n\n"
        f"RUSSIAN WHISPER SRT:\n{russian}"
    )
    return manifest, prompt


async def _generate_editorial_for_model(
    editorial: Any,
    pack_path: Path,
    *,
    model: str,
) -> dict[str, Any] | None:
    """Run the existing validated full-sermon review contract on one HIGH model."""
    try:
        from core.globals import GEMINI_CLIENTS, make_text_config_smart
    except Exception:
        return None
    if not GEMINI_CLIENTS:
        return None

    manifest, prompt = _editorial_prompt(editorial, Path(pack_path))
    config = make_text_config_smart(
        max_output_tokens=12000,
        model_name=model,
        thinking_level="high",
        response_mime_type="application/json",
        response_schema=editorial._gemini_schema(),
    )
    try:
        timeout = float(
            os.getenv("SHORTS_FACTORY_EDITORIAL_GEMINI_TIMEOUT_SEC", "300") or "300"
        )
    except (TypeError, ValueError):
        timeout = 300.0
    if not math.isfinite(timeout):
        timeout = 300.0
    timeout = max(60.0, min(timeout, 600.0))

    clients = list(GEMINI_CLIENTS)[: editorial._gemini_max_attempts()]
    for client_index, client in enumerate(clients, 1):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                ),
                timeout=timeout,
            )
            data = json.loads(editorial._strip_json_fence(getattr(response, "text", "")))
            if not isinstance(data, dict):
                continue
            review = {
                "schema_name": editorial.REVIEW_SCHEMA_NAME,
                "schema_version": editorial.REVIEW_SCHEMA_VERSION,
                "review_pack_id": manifest.get("review_pack_id"),
                "reviewer": f"gemini:{model}",
                "full_sermon": data.get("full_sermon"),
                "candidate_reviews": data.get("candidate_reviews") or [],
            }
            errors = editorial.validate_review_document(review, manifest)
            if errors:
                logger.warning(
                    "Factory editorial Gemini review rejected client=%d model=%s: %s",
                    client_index,
                    model,
                    "; ".join(errors[:8]),
                )
                continue
            return review
        except Exception as exc:
            logger.info(
                "Factory editorial Gemini soft-fail client=%d model=%s: %s",
                client_index,
                model,
                str(exc)[:180],
            )
    return None


def _install_editorial_model_policy() -> None:
    import services.translation_editorial_factory as editorial

    if getattr(editorial.generate_gemini_editorial_review, "_mp3bot_gemini37_route", False):
        return

    async def quality_first_editorial(pack_path):
        models = quality_model_chain()
        for index, model in enumerate(models):
            result = await _generate_editorial_for_model(
                editorial,
                Path(pack_path),
                model=model,
            )
            if result is not None:
                return result
            if index + 1 < len(models):
                logger.warning(
                    "Factory full-sermon review failed on %s/high; retrying on %s/high",
                    model,
                    models[index + 1],
                )
        return None

    quality_first_editorial._mp3bot_gemini37_route = True  # type: ignore[attr-defined]
    editorial.FACTORY_EDITORIAL_GEMINI_MODEL = PRIMARY_MODEL
    editorial.generate_gemini_editorial_review = quality_first_editorial


def install_gemini37_quality_routes() -> str:
    """Patch legacy 3.6-only seams after core globals exist, before Factory runs."""
    global _INSTALLED
    if _INSTALLED:
        return "Gemini 3.7/high primary; 3.6/high semantic fallback"

    _install_factory_model_policy()
    _install_editorial_model_policy()
    _INSTALLED = True
    logger.info(
        "🧠 Gemini semantic route: 3.7/high primary → 3.6/high fallback; "
        "no 3.5/Lite semantic downgrade"
    )
    return "Gemini 3.7/high primary; 3.6/high semantic fallback"


__all__ = [
    "FALLBACK_MODEL",
    "PRIMARY_MODEL",
    "install_gemini37_quality_routes",
    "quality_model_chain",
]
