#!/usr/bin/env python3
"""Capacity-aware Factory plan execution without quality downgrades.

This keeps the existing Gemini 3.6/HIGH three-pass contract intact while
separating model-capacity overload from per-client transient failures. A model
pass that gets 503/high-demand receives a small bounded retry on the same client
and already-uploaded audio. Persistent capacity overload still stops before a
multi-key sweep. Other retryable service errors retain client failover.
"""
from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Any

from services import shorts_factory_overload_runtime as overload_runtime

_FACTORY_CAPACITY_PASS_ATTEMPTS = 3
_FACTORY_CAPACITY_RETRY_BASE_SECONDS = 2.0
_FACTORY_CAPACITY_RETRY_MAX_SECONDS = 4.0
_FACTORY_CAPACITY_RETRY_JITTER_SECONDS = 1.0


def factory_client_retry_action(exc: BaseException) -> str:
    """Classify a failed Factory request without changing model quality.

    ``capacity`` means stop the current key sweep and preserve the retry cache;
    ``rotate`` means another configured client may still rescue the request;
    ``reset`` preserves the previous behavior for non-transient pass failures.
    """
    if overload_runtime.factory_overload_error(exc):
        return "capacity"
    if overload_runtime.factory_retryable_service_error(exc):
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
) -> Any:
    """Retry only an overloaded model pass while retaining the same upload."""
    import services.shorts_factory_candidates as candidates

    for attempt in range(1, _FACTORY_CAPACITY_PASS_ATTEMPTS + 1):
        try:
            return await overload_runtime.await_with_heartbeat(
                candidates._run_pass(
                    client,
                    model=model,
                    audio_part=audio_part,
                    prompt=prompt,
                    max_tokens=max_tokens,
                ),
                label=label,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if (
                not overload_runtime.factory_overload_error(exc)
                or attempt >= _FACTORY_CAPACITY_PASS_ATTEMPTS
            ):
                raise
            delay = _capacity_retry_delay(attempt)
            overload_runtime.logger.warning(
                "Shorts Factory HIGH pass capacity retry %d/%d after %s: %s",
                attempt + 1,
                _FACTORY_CAPACITY_PASS_ATTEMPTS,
                type(exc).__name__,
                str(exc)[:500],
            )
            await overload_runtime.safe_status(
                "⚠️ Gemini 3.6 HIGH вернула 503/high demand. "
                f"Повторяю текущий проход {attempt + 1}/"
                f"{_FACTORY_CAPACITY_PASS_ATTEMPTS} на том же ключе и уже "
                f"загруженном аудио через {delay:.1f} сек…"
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
) -> dict[str, Any]:
    """Run the unchanged 3-pass Gemini 3.6/HIGH Factory contract.

    Model-pass 503/high-demand errors get bounded same-upload retries before the
    existing capacity fast-fail. Persistent capacity errors do not re-upload the
    source across every key. 429/network/other retryable failures continue to
    rotate clients as before.
    """
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
        raise RuntimeError(
            "Gemini is unavailable; SHORTS FACTORY MAX requires Gemini 3.6"
        )

    model = candidates.shorts_factory_model()
    mime_type = factory_audio_mime_type(audio_path)
    file_size = audio_path.stat().st_size
    scout = judged = None
    last_error: BaseException | None = None
    capacity_overload = False

    for index, client in enumerate(clients, 1):
        uploaded_name = ""
        try:
            await overload_runtime.safe_status(
                f"🧠 Gemini 3.6 MAX · ключ {index}/{len(clients)}: готовлю аудио…"
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
                            display_name=(
                                f"Shorts Factory MAX — {performer} — {title}"
                            )[:500],
                        ),
                    ),
                    label=(
                        f"⬆️ Gemini 3.6 · ключ {index}/{len(clients)}: "
                        "загружаю lossless-аудио…"
                    ),
                )
                uploaded = await overload_runtime.await_with_heartbeat(
                    candidates._wait_uploaded_file(client, uploaded),
                    label=(
                        f"⏳ Gemini 3.6 · ключ {index}/{len(clients)}: "
                        "сервер обрабатывает аудио…"
                    ),
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
                        f"🧠 Gemini 3.6 HIGH · ключ {index}/{len(clients)} "
                        "· проход 1/3…"
                    ),
                )

            if judged is None:
                judged = await _run_pass_with_capacity_retry(
                    client,
                    model=model,
                    audio_part=audio_part,
                    prompt=candidates._judge_prompt(scout, duration),
                    max_tokens=28000,
                    label=(
                        f"🧠 Gemini 3.6 HIGH · ключ {index}/{len(clients)} "
                        "· проход 2/3…"
                    ),
                )

            audited = await _run_pass_with_capacity_retry(
                client,
                model=model,
                audio_part=audio_part,
                prompt=candidates._boundary_prompt(judged, duration),
                max_tokens=28000,
                label=(
                    f"🧠 Gemini 3.6 HIGH · ключ {index}/{len(clients)} "
                    "· проход 3/3…"
                ),
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
            if not gated.get("shorts_candidates") and not gated.get(
                "long_candidates"
            ):
                raise RuntimeError(
                    "No candidates passed the final Factory MAX quality gate"
                )
            return gated

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            action = factory_client_retry_action(exc)
            overload_runtime.logger.warning(
                "Shorts Factory capacity-aware client %d/%d failed: %s: %s",
                index,
                len(clients),
                type(exc).__name__,
                str(exc)[:500],
            )
            if action == "capacity":
                capacity_overload = True
                await overload_runtime.safe_status(
                    "⚠️ Gemini 3.6 вернула 503/high demand после ограниченных "
                    "повторов текущего HIGH-прохода. Не загружаю то же "
                    "lossless-аудио на остальные ключи: качество не понижаю, "
                    "retry-кэш сохранён."
                )
                break
            if action == "rotate":
                await overload_runtime.safe_status(
                    f"⚠️ Gemini 3.6 временно недоступна на ключе "
                    f"{index}/{len(clients)}. Переключаю ключ без повторения "
                    "уже завершённых проходов…"
                )
                continue
            scout = judged = None
        finally:
            if uploaded_name:
                try:
                    await client.aio.files.delete(name=uploaded_name)
                except Exception:
                    pass

    if capacity_overload:
        raise RuntimeError(
            "Gemini 3.6 сейчас перегружена (503/high demand). "
            "Ограниченные повторы текущего HIGH-прохода исчерпаны; перебор "
            "остальных API-ключей остановлен, чтобы не повторять дорогую "
            "загрузку того же lossless-аудио. Качество не понижено: 3.5/2.x "
            "не использовались. Lossless-аудио сохранено в retry-кэше примерно на "
            f"{overload_runtime.cache_ttl_seconds() / 3600:.0f} ч — "
            "повторите Factory позже."
        ) from last_error

    raise RuntimeError(
        f"All Gemini clients failed strict Shorts Factory review: {last_error}"
    )


__all__ = ["create_factory_plan_resumable", "factory_client_retry_action"]
