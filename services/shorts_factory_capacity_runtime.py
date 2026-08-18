#!/usr/bin/env python3
"""Capacity-aware Factory plan execution without quality downgrades.

Gemini 503/high-demand retries are centralized at the shared client boundary in
``services.gemini_capacity_runtime``. Factory therefore must not multiply the
same backend-capacity failure by every configured API key or re-upload the same
large analysis file to each client. Quota/auth/network failures retain their
separate semantics; quality is never downgraded.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from services import shorts_factory_capacity as capacity
from services.gemini_capacity_runtime import is_capacity_terminal_error

logger = logging.getLogger(__name__)


def factory_client_retry_action(exc: BaseException) -> str:
    if is_capacity_terminal_error(exc):
        return "stop_capacity"
    if capacity.factory_overload_error(exc):
        return "stop_capacity"
    if capacity.factory_retryable_service_error(exc):
        return "rotate"
    return "reset"


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
    """Run one Factory pass; shared Gemini client owns bounded 503 retry/backoff."""
    import services.shorts_factory_candidates as candidates

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


async def create_factory_plan_resumable(
    audio_path: Path,
    *,
    title: str,
    performer: str,
    duration: int,
    source_language: str = "",
    status_msg: Any = None,
) -> dict[str, Any]:
    """Run strict three-pass Factory planning with bounded capacity handling."""
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
    capacity_overload = False

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
            logger.warning(
                "Shorts Factory client %d/%d failed: %s: %s",
                index,
                len(clients),
                type(exc).__name__,
                str(exc)[:500],
            )
            if action == "stop_capacity":
                capacity_overload = True
                await capacity.safe_status(
                    status_msg,
                    "⚠️ Gemini 3.7 сейчас не принимает production-запрос после "
                    "ограниченных повторов. Не умножаю тот же 503 по другим ключам "
                    "и не понижаю модель; analysis-аудио останется в retry-кэше.",
                )
                break
            if action == "rotate":
                await capacity.safe_status(
                    status_msg,
                    f"⚠️ Временная клиентская/сетевая ошибка на клиенте "
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

    if capacity_overload:
        raise RuntimeError(
            "Gemini 3.7 capacity временно не приняла Factory MAX после bounded "
            "backoff. Это не исчерпание API-ключей: при 503 ротация ключей "
            "намеренно не умножается. Качество не понижено: 3.6/3.5/Lite не "
            "использовались. Analysis-аудио сохранено в retry-кэше примерно на "
            f"{capacity.retry_cache_ttl_seconds() / 3600:.0f} ч — повторите Factory позже."
        ) from last_error

    raise RuntimeError(
        f"All Gemini clients failed strict Shorts Factory review: {last_error}"
    )


__all__ = ["create_factory_plan_resumable", "factory_client_retry_action"]