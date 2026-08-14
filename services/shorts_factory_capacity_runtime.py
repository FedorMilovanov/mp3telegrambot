#!/usr/bin/env python3
"""Capacity-aware Factory execution with quality-preserving model fallback.

Factory uses Gemini 3.7 Flash/high first. A pass that receives explicit
503/high-demand gets a small bounded retry on the same client and the same
already-uploaded audio. If 3.7 remains capacity-bound, the exact same pass is
continued on Gemini 3.6 Flash/high using that same upload. 3.5/Lite is never a
candidate-selection fallback. Persistent 3.6 capacity overload still fails fast
before a wasteful multi-key upload sweep and leaves the existing retry cache for
later resume.
"""
from __future__ import annotations

import asyncio
import os
import random
from pathlib import Path
from typing import Any

from services import shorts_factory_overload_runtime as overload_runtime

_FACTORY_CAPACITY_PASS_ATTEMPTS = 3
_FACTORY_CAPACITY_RETRY_BASE_SECONDS = 2.0
_FACTORY_CAPACITY_RETRY_MAX_SECONDS = 4.0
_FACTORY_CAPACITY_RETRY_JITTER_SECONDS = 1.0
_PRIMARY_MODEL = "gemini-3.7-flash"
_FALLBACK_MODEL = "gemini-3.6-flash"
_ALLOWED_MODELS = {_PRIMARY_MODEL, _FALLBACK_MODEL}


def factory_client_retry_action(exc: BaseException) -> str:
    """Classify a failed Factory request without changing semantic quality."""
    if overload_runtime.factory_overload_error(exc):
        return "capacity"
    if overload_runtime.factory_retryable_service_error(exc):
        return "rotate"
    return "reset"


def _factory_model_chain() -> tuple[str, ...]:
    primary = os.getenv("SHORTS_FACTORY_MODEL", _PRIMARY_MODEL).strip() or _PRIMARY_MODEL
    if primary not in _ALLOWED_MODELS:
        raise RuntimeError(
            "SHORTS FACTORY MAX requires gemini-3.7-flash/high with "
            f"gemini-3.6-flash/high fallback; got {primary!r}"
        )
    raw = os.getenv("SHORTS_FACTORY_FALLBACK_MODELS", _FALLBACK_MODEL)
    models = [primary]
    for item in str(raw or "").split(","):
        model = item.strip()
        if not model or model in models:
            continue
        if model not in _ALLOWED_MODELS:
            raise RuntimeError(
                "Factory semantic fallback may only use gemini-3.6-flash/high; "
                f"got {model!r}"
            )
        models.append(model)
    return tuple(models)


def _capacity_retry_delay(attempt: int) -> float:
    backoff = min(
        _FACTORY_CAPACITY_RETRY_MAX_SECONDS,
        _FACTORY_CAPACITY_RETRY_BASE_SECONDS * (2 ** max(0, attempt - 1)),
    )
    return backoff + random.uniform(0.0, _FACTORY_CAPACITY_RETRY_JITTER_SECONDS)


async def _run_pass_with_capacity_fallback(
    client: Any,
    *,
    models: tuple[str, ...],
    model_index: int,
    audio_part: Any,
    prompt: str,
    max_tokens: int,
    pass_number: int,
    client_index: int,
    client_count: int,
) -> tuple[Any, int]:
    """Run one HIGH pass, falling forward only after persistent model capacity."""
    import services.shorts_factory_candidates as candidates

    index = model_index
    while index < len(models):
        model = models[index]
        for attempt in range(1, _FACTORY_CAPACITY_PASS_ATTEMPTS + 1):
            try:
                response = await overload_runtime.await_with_heartbeat(
                    candidates._run_pass(
                        client,
                        model=model,
                        audio_part=audio_part,
                        prompt=prompt,
                        max_tokens=max_tokens,
                    ),
                    label=(
                        f"🧠 {model} HIGH · ключ {client_index}/{client_count} "
                        f"· проход {pass_number}/3…"
                    ),
                )
                return response, index
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not overload_runtime.factory_overload_error(exc):
                    raise
                if attempt < _FACTORY_CAPACITY_PASS_ATTEMPTS:
                    delay = _capacity_retry_delay(attempt)
                    overload_runtime.logger.warning(
                        "Shorts Factory %s HIGH pass %d capacity retry %d/%d: %s",
                        model,
                        pass_number,
                        attempt + 1,
                        _FACTORY_CAPACITY_PASS_ATTEMPTS,
                        str(exc)[:500],
                    )
                    await overload_runtime.safe_status(
                        f"⚠️ {model} HIGH вернула 503/high demand. "
                        f"Повторяю проход {pass_number}/3 на том же ключе и уже "
                        f"загруженном аудио через {delay:.1f} сек…"
                    )
                    await asyncio.sleep(delay)
                    continue

                # Persistent capacity is model feedback. Move to the next allowed
                # quality model without re-uploading the file or discarding prior
                # completed semantic passes.
                if index + 1 < len(models):
                    fallback = models[index + 1]
                    await overload_runtime.safe_status(
                        f"⚠️ {model} остаётся перегружена. Продолжаю тот же HIGH-"
                        f"проход {pass_number}/3 на {fallback} без повторной "
                        "загрузки аудио и без снижения thinking level."
                    )
                    overload_runtime.logger.warning(
                        "Factory capacity fallback %s -> %s on pass %d; same upload retained",
                        model,
                        fallback,
                        pass_number,
                    )
                    index += 1
                    break
                raise
        else:  # pragma: no cover - loop always returns, raises or advances
            raise AssertionError("unreachable")

    raise RuntimeError("Factory exhausted the configured quality model chain")


async def create_factory_plan_resumable(
    audio_path: Path,
    *,
    title: str,
    performer: str,
    duration: int,
    source_language: str = "",
) -> dict[str, Any]:
    """Run the strict three-pass 3.7/high -> 3.6/high Factory contract."""
    import services.shorts_factory_candidates as candidates
    from services.shorts_factory_quality_gate import (
        apply_factory_quality_gate,
        validated_factory_plan_language,
    )
    from services.shorts_factory_source import factory_audio_mime_type

    audio_path = Path(audio_path)
    if not audio_path.is_file() or audio_path.stat().st_size < 1024:
        raise RuntimeError("Audio file for Shorts Factory is missing or empty")

    clients = overload_runtime.factory_gemini_clients()
    if not clients or candidates.types is None:
        raise RuntimeError("Gemini is unavailable; SHORTS FACTORY MAX requires Gemini 3.7/3.6")

    models = _factory_model_chain()
    mime_type = factory_audio_mime_type(audio_path)
    file_size = audio_path.stat().st_size
    scout = judged = None
    model_index = 0
    models_used: list[str] = []
    last_error: BaseException | None = None
    capacity_overload = False

    for client_index, client in enumerate(clients, 1):
        uploaded_name = ""
        try:
            active_model = models[model_index]
            await overload_runtime.safe_status(
                f"🧠 {active_model} MAX · ключ {client_index}/{len(clients)}: готовлю аудио…"
            )
            if file_size <= 18 * 1024 * 1024:
                audio_part = candidates.types.Part.from_bytes(
                    data=audio_path.read_bytes(), mime_type=mime_type
                )
            else:
                uploaded = await overload_runtime.await_with_heartbeat(
                    client.aio.files.upload(
                        file=audio_path,
                        config=candidates.types.UploadFileConfig(
                            mime_type=mime_type,
                            display_name=(f"Shorts Factory MAX — {performer} — {title}")[:500],
                        ),
                    ),
                    label=(
                        f"⬆️ {active_model} · ключ {client_index}/{len(clients)}: "
                        "загружаю analysis-аудио один раз…"
                    ),
                )
                uploaded = await overload_runtime.await_with_heartbeat(
                    candidates._wait_uploaded_file(client, uploaded),
                    label=(
                        f"⏳ {active_model} · ключ {client_index}/{len(clients)}: "
                        "сервер обрабатывает аудио…"
                    ),
                )
                audio_part = uploaded
                uploaded_name = str(getattr(uploaded, "name", "") or "")

            if scout is None:
                scout, model_index = await _run_pass_with_capacity_fallback(
                    client,
                    models=models,
                    model_index=model_index,
                    audio_part=audio_part,
                    prompt=candidates._scout_prompt(title, performer, duration, source_language),
                    max_tokens=32000,
                    pass_number=1,
                    client_index=client_index,
                    client_count=len(clients),
                )
                if models[model_index] not in models_used:
                    models_used.append(models[model_index])

            if judged is None:
                judged, model_index = await _run_pass_with_capacity_fallback(
                    client,
                    models=models,
                    model_index=model_index,
                    audio_part=audio_part,
                    prompt=candidates._judge_prompt(scout, duration),
                    max_tokens=28000,
                    pass_number=2,
                    client_index=client_index,
                    client_count=len(clients),
                )
                if models[model_index] not in models_used:
                    models_used.append(models[model_index])

            audited, model_index = await _run_pass_with_capacity_fallback(
                client,
                models=models,
                model_index=model_index,
                audio_part=audio_part,
                prompt=candidates._boundary_prompt(judged, duration),
                max_tokens=28000,
                pass_number=3,
                client_index=client_index,
                client_count=len(clients),
            )
            if models[model_index] not in models_used:
                models_used.append(models[model_index])

            plan = candidates.validate_factory_plan(audited, duration, require_verified=True)
            if not plan["shorts_candidates"] and not plan["long_candidates"]:
                raise RuntimeError("Three-pass Gemini review produced no verified candidates")

            plan.update(
                model=models[model_index],
                primary_model=models[0],
                models_used=models_used,
                thinking_level="high",
                review_passes=3,
                strict_quality=True,
                semantic_downgrade=False,
                audio_mime_type=mime_type,
            )
            gated = apply_factory_quality_gate(plan)
            gated.setdefault("metadata", {})["language"] = validated_factory_plan_language(gated)
            if not gated.get("shorts_candidates") and not gated.get("long_candidates"):
                raise RuntimeError("No candidates passed the final Factory MAX quality gate")
            return gated

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            action = factory_client_retry_action(exc)
            overload_runtime.logger.warning(
                "Shorts Factory capacity-aware client %d/%d failed: %s: %s",
                client_index,
                len(clients),
                type(exc).__name__,
                str(exc)[:500],
            )
            if action == "capacity":
                capacity_overload = True
                await overload_runtime.safe_status(
                    f"⚠️ {models[model_index]} остаётся перегружена после bounded retry. "
                    "Не загружаю то же аудио на остальные ключи; 3.5/Lite не использую, "
                    "retry-кэш сохранён для продолжения позже."
                )
                break
            if action == "rotate":
                await overload_runtime.safe_status(
                    f"⚠️ {models[model_index]} временно недоступна на ключе "
                    f"{client_index}/{len(clients)}. Переключаю ключ и сохраняю "
                    "уже завершённые HIGH-проходы…"
                )
                continue
            scout = judged = None
            model_index = 0
            models_used.clear()
        finally:
            if uploaded_name:
                try:
                    await client.aio.files.delete(name=uploaded_name)
                except Exception:
                    pass

    if capacity_overload:
        chain = " → ".join(models)
        raise RuntimeError(
            f"Gemini quality chain {chain} сейчас перегружена (503/high demand). "
            "Bounded same-upload retries исчерпаны; перебор остальных API-ключей "
            "остановлен, чтобы не повторять дорогую загрузку. Качество не понижено: "
            "3.5/Lite не использовались. Analysis-аудио сохранено в retry-кэше примерно на "
            f"{overload_runtime.cache_ttl_seconds() / 3600:.0f} ч — повторите Factory позже."
        ) from last_error

    raise RuntimeError(
        f"All Gemini clients failed strict Shorts Factory review: {last_error}"
    )


__all__ = [
    "create_factory_plan_resumable",
    "factory_client_retry_action",
]
