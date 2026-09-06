#!/usr/bin/env python3
"""Capacity-aware Factory plan execution without quality downgrades.

This keeps the Gemini 3.8/HIGH three-pass contract intact while separating
Files API capacity from model-inference capacity. No ambient request state or
runtime rebinding is used.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from services import gemini_capacity_control as capacity_control
from services import shorts_factory_capacity as capacity

logger = logging.getLogger(__name__)

# Application-owned retry window matching the SDK's initial request + four
# transient retries. A backend-wide 503 stays on the same client/upload for the
# complete window. A quota/client-domain failure is refunded from that capacity
# window and may probe the next configured credential, but it never resets the
# already-consumed backend retry budget.
_FACTORY_CAPACITY_PASS_ATTEMPTS = 5
_FACTORY_INFERENCE_TRANSIENT_ATTEMPTS = 5
_FACTORY_UPLOAD_WAIT_SECONDS = 600.0


def factory_client_retry_action(exc: BaseException) -> str:
    if capacity.factory_overload_error(exc):
        return "capacity"
    if capacity.factory_quota_error(exc):
        return "quota"
    if capacity.factory_retryable_service_error(exc):
        return "rotate"
    return "reset"


def _capacity_retry_delay(attempt: int) -> float:
    return capacity_control.transient_retry_delay(attempt)


async def _wait_factory_upload(client: Any, uploaded: Any) -> Any:
    """Poll one Factory remote file under the Files failure domain."""
    started = asyncio.get_running_loop().time()
    current = uploaded
    while str(getattr(current, "state", "")).upper().endswith("PROCESSING"):
        if asyncio.get_running_loop().time() - started > _FACTORY_UPLOAD_WAIT_SECONDS:
            raise TimeoutError("Gemini Factory audio processing exceeded 600 seconds")
        await asyncio.sleep(3)
        current = await capacity_control.run_heavy_gemini_call(
            lambda _name=current.name: client.aio.files.get(name=_name),
            domain="files",
        )
    if str(getattr(current, "state", "")).upper().endswith("FAILED"):
        raise RuntimeError("Gemini Factory audio processing FAILED")
    return current


async def _run_pass_with_capacity_retry(
    client: Any,
    *,
    model: str,
    audio_part: Any,
    prompt: str,
    max_tokens: int,
    label: str,
    retry_budget: capacity_control.GeminiRetryBudget,
    status_msg: Any = None,
) -> Any:
    """Retry backend-wide 503 on the same client/upload within one capacity window."""
    import services.shorts_factory_candidates as candidates

    last_error: BaseException | None = None
    for client_attempt in range(1, _FACTORY_CAPACITY_PASS_ATTEMPTS + 1):
        if retry_budget.exhausted:
            if last_error is not None:
                raise last_error
            raise RuntimeError("Gemini transient retry budget exhausted")

        retry_budget.claim()
        try:
            return await capacity_control.run_heavy_gemini_call(
                lambda: capacity.await_with_heartbeat(
                    candidates._run_pass(
                        client,
                        model=model,
                        audio_part=audio_part,
                        prompt=prompt,
                        max_tokens=max_tokens,
                    ),
                    label=label,
                    status_msg=status_msg,
                ),
                domain="inference",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            if not capacity.factory_overload_error(exc):
                raise
            if client_attempt >= _FACTORY_CAPACITY_PASS_ATTEMPTS or retry_budget.exhausted:
                raise

            delay = _capacity_retry_delay(retry_budget.used)
            capacity_control.trip_overload_circuit(domain="inference", seconds=delay)
            logger.warning(
                "Shorts Factory HIGH pass capacity retry %d/%d; window attempt %d/%d after %s: %s",
                client_attempt + 1,
                _FACTORY_CAPACITY_PASS_ATTEMPTS,
                retry_budget.used + 1,
                retry_budget.limit,
                type(exc).__name__,
                str(exc)[:500],
            )
            await capacity.safe_status(
                status_msg,
                "⚠️ Gemini 3.8 HIGH вернула 503/high demand. "
                "Повторяю текущий проход на том же клиенте и уже "
                f"загруженном analysis-аудио через {delay:.1f} сек; попытка "
                f"{retry_budget.used + 1}/{retry_budget.limit}…",
            )
            await asyncio.sleep(delay)

    raise last_error or AssertionError("unreachable")


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
    if duration > 0 and not factory_duration_matches(verified_audio_duration, float(duration)):
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
        raise RuntimeError("Gemini is unavailable; SHORTS FACTORY MAX requires Gemini 3.8")

    model = candidates.shorts_factory_model()
    mime_type = factory_audio_mime_type(audio_path)
    file_size = audio_path.stat().st_size
    scout = judged = None
    last_error: BaseException | None = None
    client_outcomes: list[str] = []
    attempted_clients = 0
    capacity_budget_exhausted = False
    exhausted_stage = ""
    exhausted_attempt_limit = 0

    upload_budget = capacity_control.GeminiRetryBudget()
    scout_budget = capacity_control.GeminiRetryBudget(limit=_FACTORY_INFERENCE_TRANSIENT_ATTEMPTS)
    judge_budget = capacity_control.GeminiRetryBudget(limit=_FACTORY_INFERENCE_TRANSIENT_ATTEMPTS)
    audit_budget = capacity_control.GeminiRetryBudget(limit=_FACTORY_INFERENCE_TRANSIENT_ATTEMPTS)
    upload_in_progress = False

    def active_budget() -> capacity_control.GeminiRetryBudget:
        if upload_in_progress:
            return upload_budget
        if scout is None:
            return scout_budget
        if judged is None:
            return judge_budget
        return audit_budget

    def active_stage() -> str:
        if upload_in_progress:
            return "Gemini Files upload"
        if scout is None:
            return "Factory HIGH pass 1/3"
        if judged is None:
            return "Factory HIGH pass 2/3"
        return "Factory HIGH pass 3/3"

    for index, client in enumerate(clients, 1):
        attempted_clients += 1
        uploaded_name = ""
        try:
            await capacity.safe_status(
                status_msg,
                f"🧠 Gemini 3.8 MAX · клиент {index}/{len(clients)}: готовлю аудио…",
            )
            if file_size <= 18 * 1024 * 1024:
                upload_in_progress = False
                audio_part = candidates.types.Part.from_bytes(
                    data=audio_path.read_bytes(), mime_type=mime_type
                )
            else:
                upload_in_progress = True
                if upload_budget.exhausted:
                    break
                upload_budget.claim()
                uploaded = await capacity_control.run_heavy_gemini_call(
                    lambda _client=client, _index=index: capacity.await_with_heartbeat(
                        _client.aio.files.upload(
                            file=audio_path,
                            config=candidates.types.UploadFileConfig(
                                mime_type=mime_type,
                                display_name=(f"Shorts Factory MAX — {performer} — {title}")[:500],
                            ),
                        ),
                        label=(
                            f"⬆️ Gemini Files · клиент {_index}/{len(clients)}: "
                            "загружаю analysis-аудио…"
                        ),
                        status_msg=status_msg,
                    ),
                    domain="files",
                )
                uploaded_name = str(getattr(uploaded, "name", "") or "")
                uploaded = await capacity.await_with_heartbeat(
                    _wait_factory_upload(client, uploaded),
                    label=(
                        f"⏳ Gemini Files · клиент {index}/{len(clients)}: "
                        "сервер обрабатывает аудио…"
                    ),
                    status_msg=status_msg,
                )
                audio_part = uploaded
                upload_in_progress = False

            if scout is None:
                scout = await _run_pass_with_capacity_retry(
                    client,
                    model=model,
                    audio_part=audio_part,
                    prompt=candidates._scout_prompt(title, performer, duration, source_language),
                    max_tokens=32000,
                    label=(
                        f"🧠 Gemini 3.8 HIGH · клиент {index}/{len(clients)} · проход 1/3…"
                    ),
                    retry_budget=scout_budget,
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
                        f"🧠 Gemini 3.8 HIGH · клиент {index}/{len(clients)} · проход 2/3…"
                    ),
                    retry_budget=judge_budget,
                    status_msg=status_msg,
                )

            audited = await _run_pass_with_capacity_retry(
                client,
                model=model,
                audio_part=audio_part,
                prompt=_strict_boundary_prompt(candidates._boundary_prompt(judged, duration)),
                max_tokens=28000,
                label=(
                    f"🧠 Gemini 3.8 HIGH · клиент {index}/{len(clients)} · проход 3/3…"
                ),
                retry_budget=audit_budget,
                status_msg=status_msg,
            )
            plan = candidates.validate_factory_plan(audited, duration, require_verified=True)
            if not plan["shorts_candidates"] and not plan["long_candidates"]:
                raise RuntimeError("Three-pass Gemini review produced no verified candidates")

            plan.update(
                model=model,
                thinking_level="high",
                review_passes=3,
                strict_quality=True,
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
            budget = active_budget()
            stage = active_stage()
            domain = "files" if upload_in_progress else "inference"
            client_outcomes.append(action)
            logger.warning(
                "Shorts Factory capacity-aware client %d/%d failed at %s: %s: %s",
                index,
                len(clients),
                stage,
                type(exc).__name__,
                str(exc)[:500],
            )
            if action == "capacity":
                delay = _capacity_retry_delay(max(1, budget.used))
                capacity_control.note_overload(delay, domain=domain)
                if budget.exhausted:
                    capacity_budget_exhausted = True
                    exhausted_stage = stage
                    exhausted_attempt_limit = budget.limit
                    retry_count = max(0, budget.limit - 1)
                    await capacity.safe_status(
                        status_msg,
                        f"⚠️ {stage}: исчерпаны {budget.limit} сетевых попыток "
                        f"(initial + {retry_count} retry). Новые ключи не будут "
                        "умножать тот же 503 storm.",
                    )
                    break
                if index < len(clients):
                    await capacity.safe_status(
                        status_msg,
                        f"⚠️ {stage} вернул 503/high demand на клиенте "
                        f"{index}/{len(clients)}. Переключаю клиент через "
                        f"{delay:.1f} сек в пределах единого budget "
                        f"{budget.used + 1}/{budget.limit}…",
                    )
                    await asyncio.sleep(delay)
                    continue
                break
            if action == "quota":
                budget.refund_last_claim()
                logger.info(
                    "Shorts Factory quota/client-domain failover at %s: "
                    "capacity budget remains %d/%d used",
                    stage,
                    budget.used,
                    budget.limit,
                )
                await capacity.safe_status(
                    status_msg,
                    f"⚠️ {stage}: клиент {index}/{len(clients)} исчерпал quota/rate-limit. "
                    "Переключаю credential; этот 429 не расходует backend 503 budget…",
                )
                continue
            if action == "rotate":
                if budget.exhausted:
                    break
                await capacity.safe_status(
                    status_msg,
                    f"⚠️ {stage} временно недоступен на клиенте "
                    f"{index}/{len(clients)}. Переключаю клиент без повторения "
                    "уже завершённых проходов…",
                )
                continue
            scout = judged = None
        finally:
            upload_in_progress = False
            if uploaded_name:
                try:
                    await client.aio.files.delete(name=uploaded_name)
                except Exception:
                    pass

    capacity_failures = sum(1 for outcome in client_outcomes if outcome == "capacity")
    if capacity_budget_exhausted and last_error is not None:
        attempt_limit = exhausted_attempt_limit or capacity_control.transient_attempt_limit()
        retry_count = max(0, attempt_limit - 1)
        raise RuntimeError(
            "Gemini сейчас перегружен (503/high demand). "
            f"Для этапа {exhausted_stage or 'Factory'} исчерпан единый лимит "
            f"{attempt_limit} сетевых попыток "
            f"(initial + максимум {retry_count} retry) без умножения по API-ключам. "
            "Это НЕ означает, что API-ключи или квота исчерпаны: 503 — ошибка "
            "доступности backend, а quota/rate-limit обычно возвращается как 429. "
            f"Проверено клиентов: {attempted_clients}/{len(clients)}. "
            "Качество не понижено: 3.7/3.6/3.5/Lite не использовались. "
            "Analysis-аудио сохранено в retry-кэше примерно на "
            f"{capacity.retry_cache_ttl_seconds() / 3600:.0f} ч — повторите Factory позже."
        ) from last_error

    if capacity_failures:
        other_failures = len(client_outcomes) - capacity_failures
        raise RuntimeError(
            "Gemini strict Factory review failed across attempted clients: "
            f"attempted={attempted_clients}/{len(clients)}, "
            f"{capacity_failures} capacity failure(s), {other_failures} other failure(s). "
            "503 is backend availability, not proof of exhausted keys/quota. "
            "Качество не понижено: 3.7/3.6/3.5/Lite не использовались."
        ) from last_error

    raise RuntimeError(
        "All attempted Gemini clients failed strict Shorts Factory review: "
        f"attempted={attempted_clients}/{len(clients)}; last_error={last_error}"
    )


__all__ = ["create_factory_plan_resumable", "factory_client_retry_action"]
