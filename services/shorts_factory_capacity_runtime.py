#!/usr/bin/env python3
"""Capacity-aware Factory plan execution without quality downgrades.

This keeps the Gemini 3.7/HIGH three-pass contract intact while separating
model-capacity overload from per-client transient failures. No ambient request
state or runtime rebinding is used.
"""
from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path
from typing import Any

from services import shorts_factory_capacity as capacity

logger = logging.getLogger(__name__)

# A 503 is backend capacity pressure, not proof that an API key/quota is
# exhausted. Retrying the same expensive HIGH request four times per client made
# Factory spend many minutes hammering the same overloaded route before rotating.
# Two attempts per client still provide exponential recovery while keeping the
# full Gemini 3.7/HIGH three-pass quality contract and trying every configured
# client. There is no lower-model fallback here.
_FACTORY_CAPACITY_PASS_ATTEMPTS = 2
_FACTORY_CAPACITY_RETRY_BASE_SECONDS = 15.0
_FACTORY_CAPACITY_RETRY_MAX_SECONDS = 60.0
_FACTORY_CAPACITY_RETRY_JITTER_SECONDS = 5.0


def factory_client_retry_action(exc: BaseException) -> str:
    if capacity.factory_overload_error(exc):
        return "capacity"
    if capacity.factory_retryable_service_error(exc):
        return "rotate"
    return "reset"


def _capacity_retry_delay(attempt: int) -> float:
    backoff = min(
        _FACTORY_CAPACITY_RETRY_MAX_SECONDS,
        _FACTORY_CAPACITY_RETRY_BASE_SECONDS * (2 ** max(0, attempt - 1)),
    )
    return backoff + random.uniform(0.0, _FACTORY_CAPACITY_RETRY_JITTER_SECONDS)


async def _run_pass_with_capacity_retry(
    client: Any,
    *,
    model: str,
    audio_part: Any,
    prompt: str,
    max_tokens: int,
    label: str,
    status_msg: Any = None,
) -> Any:
    """Retry only an overloaded model pass while retaining the same upload."""
    import services.shorts_factory_candidates as candidates

    for attempt in range(1, _FACTORY_CAPACITY_PASS_ATTEMPTS + 1):
        try:
            return await capacity.await_with_heartbeat(
                candidates._run_pass(
                    client,
                    model=model,
                    audio_part=audio_part,
                    prompt=prompt,
                    max_tokens=max_tokens,
                ),
                label=label,
                status_msg=status_msg,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if (
                not capacity.factory_overload_error(exc)
                or attempt >= _FACTORY_CAPACITY_PASS_ATTEMPTS
            ):
                raise
            delay = _capacity_retry_delay(attempt)
            logger.warning(
                "Shorts Factory HIGH pass capacity retry %d/%d after %s: %s",
                attempt + 1,
                _FACTORY_CAPACITY_PASS_ATTEMPTS,
                type(exc).__name__,
                str(exc)[:500],
            )
            await capacity.safe_status(
                status_msg,
                "⚠️ Gemini 3.7 HIGH вернула 503/high demand. "
                f"Повторяю текущий проход {attempt + 1}/"
                f"{_FACTORY_CAPACITY_PASS_ATTEMPTS} на том же клиенте и уже "
                f"загруженном analysis-аудио через {delay:.1f} сек…",
            )
            await asyncio.sleep(delay)

    raise AssertionError("unreachable")


async def create_factory_plan_resumable(
    audio_path: Path,
    *,
    title: str,
    performer: str,
    duration: int,
    source_language: str = "",
    status_msg: Any = None,
) -> dict[str, Any]:
    """Run strict three-pass Factory planning with bounded capacity retries."""
    import services.shorts_factory_candidates as candidates
    from services.shorts_factory_source import (
        _strict_boundary_prompt,
        factory_audio_mime_type,
        factory_duration_matches,
        measure_factory_audio_duration,
    )
    from services.shorts_factory_quality_gate import (
        apply_factory_quality_gate,
        validated_factory_plan_language,
    )

    audio_path = Path(audio_path)
    if not audio_path.is_file() or audio_path.stat().st_size < 1024:
        raise RuntimeError("Audio file for Shorts Factory is missing or empty")

    verified_audio_duration = await measure_factory_audio_duration(audio_path)
    if duration > 0 and not factory_duration_matches(
        verified_audio_duration,
        float(duration),
    ):
        raise RuntimeError(
            "Factory analysis audio duration does not match yt-dlp source metadata: "
            f"metadata={float(duration):.3f}s verified={verified_audio_duration:.3f}s"
        )
    logger.info(
        "Factory analysis audio duration verified before Gemini: metadata=%.3fs decoded=%.3fs",
        float(duration or 0),
        verified_audio_duration,
    )

    clients = capacity.factory_gemini_clients()
    if not clients or candidates.types is None:
        raise RuntimeError(
            "Gemini is unavailable; SHORTS FACTORY MAX requires Gemini 3.7"
        )

    model = candidates.shorts_factory_model()
    mime_type = factory_audio_mime_type(audio_path)
    file_size = audio_path.stat().st_size
    scout = judged = None
    last_error: BaseException | None = None
    client_outcomes: list[str] = []

    for index, client in enumerate(clients, 1):
        uploaded_name = ""
        try:
            await capacity.safe_status(
                status_msg,
                f"🧠 Gemini 3.7 MAX · клиент {index}/{len(clients)}: готовлю аудио…",
            )
            if file_size <= 18 * 1024 * 1024:
                audio_part = candidates.types.Part.from_bytes(
                    data=audio_path.read_bytes(),
                    mime_type=mime_type,
                )
            else:
                uploaded = await capacity.await_with_heartbeat(
                    client.aio.files.upload(
                        file=audio_path,
                        config=candidates.types.UploadFileConfig(
                            mime_type=mime_type,
                            display_name=(
                                f"Shorts Factory MAX — {performer} — {title}"
                            )[:500],
                        ),
                    ),
                    label=(
                        f"⬆️ Gemini 3.7 · клиент {index}/{len(clients)}: "
                        "загружаю analysis-аудио…"
                    ),
                    status_msg=status_msg,
                )
                uploaded = await capacity.await_with_heartbeat(
                    candidates._wait_uploaded_file(client, uploaded),
                    label=(
                        f"⏳ Gemini 3.7 · клиент {index}/{len(clients)}: "
                        "сервер обрабатывает аудио…"
                    ),
                    status_msg=status_msg,
                )
                audio_part = uploaded
                uploaded_name = str(getattr(uploaded, "name", "") or "")

            if scout is None:
                scout = await _run_pass_with_capacity_retry(
                    client,
                    model=model,
                    audio_part=audio_part,
                    prompt=candidates._scout_prompt(
                        title,
                        performer,
                        duration,
                        source_language,
                    ),
                    max_tokens=32000,
                    label=(
                        f"🧠 Gemini 3.7 HIGH · клиент {index}/{len(clients)} "
                        "· проход 1/3…"
                    ),
                    status_msg=status_msg,
                )

            if judged is None:
                judged = await _run_pass_with_capacity_retry(
                    client,
                    model=model,
                    audio_part=audio_part,
                    prompt=candidates._judge_prompt(scout, duration),
                    max_tokens=28000,
                    label=(
                        f"🧠 Gemini 3.7 HIGH · клиент {index}/{len(clients)} "
                        "· проход 2/3…"
                    ),
                    status_msg=status_msg,
                )

            audited = await _run_pass_with_capacity_retry(
                client,
                model=model,
                audio_part=audio_part,
                prompt=_strict_boundary_prompt(
                    candidates._boundary_prompt(judged, duration)
                ),
                max_tokens=28000,
                label=(
                    f"🧠 Gemini 3.7 HIGH · клиент {index}/{len(clients)} "
                    "· проход 3/3…"
                ),
                status_msg=status_msg,
            )
            plan = candidates.validate_factory_plan(
                audited,
                duration,
                require_verified=True,
            )
            if not plan["shorts_candidates"] and not plan["long_candidates"]:
                raise RuntimeError(
                    "Three-pass Gemini review produced no verified candidates"
                )

            plan.update(
                model=model,
                thinking_level="high",
                review_passes=3,
                strict_quality=True,
                audio_mime_type=mime_type,
            )
            gated = apply_factory_quality_gate(plan)
            gated.setdefault("metadata", {})["language"] = (
                validated_factory_plan_language(gated)
            )
            if not gated.get("shorts_candidates") and not gated.get("long_candidates"):
                raise RuntimeError(
                    "No candidates passed the final Factory MAX quality gate"
                )
            return gated

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            action = factory_client_retry_action(exc)
            client_outcomes.append(action)
            logger.warning(
                "Shorts Factory capacity-aware client %d/%d failed: %s: %s",
                index,
                len(clients),
                type(exc).__name__,
                str(exc)[:500],
            )
            if action == "capacity":
                if index < len(clients):
                    message = (
                        f"⚠️ Gemini 3.7 вернула 503/high demand на клиенте "
                        f"{index}/{len(clients)} после bounded HIGH-повторов. "
                        "Переключаюсь на следующий клиент без понижения модели…"
                    )
                else:
                    message = (
                        f"⚠️ Gemini 3.7 вернула 503/high demand на последнем клиенте "
                        f"{index}/{len(clients)} после bounded HIGH-повторов."
                    )
                await capacity.safe_status(status_msg, message)
                continue
            if action == "rotate":
                await capacity.safe_status(
                    status_msg,
                    f"⚠️ Gemini 3.7 временно недоступна на клиенте "
                    f"{index}/{len(clients)}. Переключаю клиент без повторения "
                    "уже завершённых проходов…",
                )
                continue
            scout = judged = None
        finally:
            if uploaded_name:
                try:
                    await client.aio.files.delete(name=uploaded_name)
                except Exception:
                    pass

    capacity_failures = sum(
        1 for outcome in client_outcomes if outcome == "capacity"
    )
    if (
        client_outcomes
        and len(client_outcomes) == len(clients)
        and capacity_failures == len(clients)
    ):
        raise RuntimeError(
            "Gemini 3.7 сейчас перегружена (503/high demand). "
            f"Все {len(clients)} настроенных API-клиента получили 503 после bounded "
            "экспоненциальных повторов. Это НЕ означает, что API-ключи или квота "
            "исчерпаны: 503 — ошибка доступности backend, а quota/rate-limit обычно "
            "возвращается как 429. Качество не понижено: 3.6/3.5/Lite не "
            "использовались. Analysis-аудио сохранено в retry-кэше примерно на "
            f"{capacity.retry_cache_ttl_seconds() / 3600:.0f} ч — повторите Factory позже."
        ) from last_error

    if capacity_failures:
        other_failures = len(client_outcomes) - capacity_failures
        raise RuntimeError(
            "Gemini 3.7 strict Factory review failed across configured clients: "
            f"{capacity_failures}/{len(clients)} client(s) returned 503/high demand; "
            f"{other_failures} client(s) failed for other reasons. "
            "503 is backend availability, not proof of exhausted keys/quota. "
            "Качество не понижено: 3.6/3.5/Lite не использовались."
        ) from last_error

    raise RuntimeError(
        f"All Gemini clients failed strict Shorts Factory review: {last_error}"
    )


__all__ = ["create_factory_plan_resumable", "factory_client_retry_action"]
