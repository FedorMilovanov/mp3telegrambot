#!/usr/bin/env python3
"""
Gemini Audio Analyzer — анализ аудио через Gemini API.

Публичный API:
    parsed_data, used_client, used_audio_part = await gemini_analyze_audio(
        mp3_path, title, performer, duration, status_msg
    )

Возвращает тройку (parsed_data, used_client, used_audio_part).
    - parsed_data: dict с результатами анализа или None при ошибке
    - used_client: Gemini-клиент, использованный для загрузки файла
    - used_audio_part: audio_part для повторного использования (конспект и т.п.)
Все три — None при фатальной ошибке.
"""
from core.globals import (
    HAS_GEMINI, GEMINI_API_KEY,
    GEMINI_CLIENTS,
    is_quota_error, is_overload_error,
    make_audio_config,
)
from core.database import GEMINI_MODEL
from core.json_parser import _parse_gemini_response, _recover_truncated_json
from core.progress import set_progress
from core.utils import format_timestamp, mask_api_key as _mask_api_key
from core.prompts import build_audio_analysis_prompt, AUDIO_ANALYSIS_MODE
from core.observability import alog_gemini_response, alog_gemini_run
from core.candidate_schema import audio_analysis_response_schema, timestamp_repair_response_schema
from core.prompt_compactor import compact_prompt_for_generation
from core.core_utils import time_to_seconds
from core.text_utils import _scrub_inline
from services import gemini_capacity_control as capacity_control

import asyncio
import logging
import re
import time
import os

try:
    from google.genai import types
except ImportError:
    types = None

logger = logging.getLogger(__name__)


def _audio_structured_output_enabled() -> bool:
    """Opt-out flag for primary audio structured JSON output."""
    return (os.getenv("AUDIO_ANALYSIS_STRUCTURED", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}


def _audio_fallback_models(primary_model: str) -> list[str]:
    """Return the single production audio model; semantic downgrade is forbidden."""
    primary = str(primary_model or "").strip()
    return [primary] if primary else []


def _audio_structured_timeout() -> float:
    """Shorter timeout for schema attempt; legacy retry keeps full timeout."""
    try:
        return max(30.0, min(float(os.getenv("AUDIO_STRUCTURED_TIMEOUT", "180")), 600.0))
    except (TypeError, ValueError):
        return 180.0


def _timestamp_repair_enabled() -> bool:
    return (os.getenv("AUDIO_TIMESTAMP_REPAIR", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}


def _is_timeout_error(exc: BaseException) -> bool:
    return (
        isinstance(exc, (asyncio.TimeoutError, TimeoutError))
        or "timeout" in type(exc).__name__.casefold()
        or "timed out" in str(exc).casefold()
    )


def _format_ts_for_prompt(seconds: int | float = 0) -> str:
    try:
        sec = max(0, int(seconds or 0))
    except (TypeError, ValueError):
        sec = 0
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _format_repaired_timestamps(data: dict, duration: int) -> str:
    items = data.get("timestamps") if isinstance(data, dict) else []
    if not isinstance(items, list):
        return ""
    lines: list[str] = []
    seen: set[str] = set()
    for item in items[:50]:
        if not isinstance(item, dict):
            continue
        t = str(item.get("time") or "").strip()
        topic = _scrub_inline(str(item.get("topic") or "").strip())
        if not t or not topic:
            continue
        sec = time_to_seconds(t)
        if sec is None or sec < 0 or (duration and sec > duration + 30):
            continue
        if t in seen:
            continue
        seen.add(t)
        lines.append(f"{_format_ts_for_prompt(sec)} {topic}")
    return "\n".join(lines)


async def _repair_timestamp_coverage_if_needed(client, audio_part, parsed: dict | None, duration: int, model_name: str, video_id: str) -> dict | None:
    """One targeted, non-fatal LOW-thinking repair pass for timestamp coverage."""
    if not parsed or not _timestamp_repair_enabled() or not parsed.get("timestamp_coverage_warning"):
        return parsed
    if client is None or audio_part is None:
        return parsed
    current = parsed.get("timestamps") or ""
    min_final = _format_ts_for_prompt(int(max(0, int(duration or 0)) * 0.88))
    _current_secs = [time_to_seconds(line.split(" ", 1)[0]) for line in str(current or "").splitlines() if line.strip()]
    _last_current = max([x for x in _current_secs if x is not None] or [0])
    _missing_from = _format_ts_for_prompt(_last_current)
    prompt = (
        "Исправь ТОЛЬКО недостающее покрытие timestamps для этого аудиоматериала. "
        f"Особенно восстанови смысловые повороты с {_missing_from} до конца; итоговый список должен покрыть весь материал. "
        f"Длительность: {_format_ts_for_prompt(duration)}. "
        f"Последний смысловой таймкод должен быть примерно не раньше {min_final}, если материал не закончился раньше. "
        "Верни JSON только вида {\"timestamps\":[{\"time\":\"M:SS\",\"topic\":\"...\"}]}. "
        "СТИЛЬ topic такой же, как в текущем списке: в каждом topic выдели "
        "**жирным** 1 ключевую фразу (2-4 слова) через **двойные звёздочки** — "
        "как в исходных строках; без точки в конце. "
        "Не меняй автора, тему и другие поля. Не делай механическую нарезку; ставь таймкоды только на смысловые повороты.\n\n"
        f"Текущий неполный список:\n{current[:4500]}"
    )
    try:
        resp = await capacity_control.run_heavy_gemini_call(
            lambda: asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=[audio_part, prompt],
                    config=make_audio_config(
                        max_output_tokens=16000,
                        model_name=model_name,
                        response_mime_type="application/json",
                        response_schema=timestamp_repair_response_schema(),
                        thinking_level="low",
                    ),
                ),
                timeout=float(os.getenv("AUDIO_TIMESTAMP_REPAIR_TIMEOUT", "240")),
            ),
            domain="inference",
        )
        raw = (resp.text or "").strip()
        import json as _json
        try:
            repaired = _json.loads(raw)
        except _json.JSONDecodeError:
            repaired = _recover_truncated_json(raw)
            if repaired is None:
                raise
        repaired_lines = _format_repaired_timestamps(repaired, duration)
        if repaired_lines and len(repaired_lines.splitlines()) >= max(3, len(str(current).splitlines())):
            parsed = {**parsed, "timestamps": repaired_lines}
            parsed.pop("timestamp_coverage_warning", None)
            logger.info("Timestamp coverage repair applied: %d lines", len(repaired_lines.splitlines()))
        await alog_gemini_response(
            response=resp, task="audio_timestamp_repair", video_id=video_id,
            model=model_name, thinking_level="low", json_valid=bool(repaired_lines),
            error="" if repaired_lines else "empty_repair",
        )
    except Exception as exc:
        if is_overload_error(exc):
            capacity_control.note_overload(
                capacity_control.transient_retry_delay(1), domain="inference"
            )
        logger.warning("Timestamp coverage repair failed non-fatally: %s: %s", type(exc).__name__, str(exc)[:180])
        await alog_gemini_run(
            task="audio_timestamp_repair", video_id=video_id, model=model_name,
            thinking_level="low", json_valid=False, error=f"{type(exc).__name__}: {str(exc)[:240]}",
        )
    return parsed


_MAX_UPLOAD_WAIT = 600


async def _safe_delete_gemini_file(client, file_name: str) -> None:
    try:
        await client.aio.files.delete(name=file_name)
    except Exception as e:
        logger.warning(f"Не удалось удалить Gemini file {file_name}: {e}")


_BG_DELETE_TASKS: set = set()


def _spawn_safe_delete(client, file_name: str) -> None:
    if not file_name or client is None:
        return
    task = asyncio.create_task(_safe_delete_gemini_file(client, file_name))
    _BG_DELETE_TASKS.add(task)
    task.add_done_callback(_BG_DELETE_TASKS.discard)


def _validate_and_fix_timestamps(data: dict, duration: int) -> dict:
    """Удаляет/обнуляет таймкоды за пределами duration из parsed_data."""
    if duration <= 0:
        return data

    ts_pattern = re.compile(r'^(\d+):(\d{2})(?::(\d{2}))?$')

    def _secs(ts_str: str):
        if not isinstance(ts_str, str):
            return None
        m = ts_pattern.match(ts_str.strip())
        if not m:
            return None
        if m.group(3) is not None:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        return int(m.group(1)) * 60 + int(m.group(2))

    def _fix_value(v):
        if isinstance(v, list):
            return [_fix_value(item) for item in v]
        if isinstance(v, dict):
            return _fix_dict(v)
        return v

    def _fix_dict(d: dict) -> dict:
        out = {}
        for key, val in d.items():
            if key == "time" and isinstance(val, str):
                secs = _secs(val)
                if secs is not None and secs > duration:
                    logger.debug(f"Timestamp {val} > duration {duration}s, обнуляем.")
                    out[key] = ""
                else:
                    out[key] = val
            elif isinstance(val, (dict, list)):
                out[key] = _fix_value(val)
            else:
                out[key] = val
        return out

    if isinstance(data, dict):
        return _fix_dict(data)
    return data


async def gemini_analyze_audio(mp3_path, title, performer, duration, status_msg, prefix=""):
    """Анализирует аудио через Gemini API без retry-storm и model downgrade."""
    if not duration or duration <= 0:
        logger.warning("gemini_analyze_audio: duration=0 или не задан, пропускаем анализ.")
        return None, None, None

    if not GEMINI_CLIENTS:
        return None, None, None

    _user_model = GEMINI_MODEL
    _models_to_try = _audio_fallback_models(_user_model)
    logger.info("Gemini audio model (strict no-downgrade): %s", _models_to_try)

    used_client = None
    used_audio_part = None
    _obs_started = time.perf_counter()
    _obs_video_id = getattr(mp3_path, "stem", "") or ""
    _obs_model = ""
    _obs_thinking_level = "high"
    _obs_retry_num = 0
    _obs_is_fallback = False

    def _obs_duration_ms() -> int:
        return int((time.perf_counter() - _obs_started) * 1000)

    try:
        file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
        await set_progress(status_msg, 4, {"info": "🧠 Загружаю аудио для анализа..."})
        audio_bytes = None

        upload_budget = capacity_control.GeminiRetryBudget()
        inference_budget = capacity_control.GeminiRetryBudget()

        # If a prior semantic operation already exhausted the shared inference
        # circuit, do not upload another large file that cannot be consumed.
        capacity_control.require_domain_available("inference")

        async def upload_to_client(client):
            """One Files API upload attempt under the shared upload budget."""
            if file_size_mb <= 0:
                return types.Part.from_bytes(data=audio_bytes, mime_type="audio/mpeg"), client
            if upload_budget.exhausted:
                raise RuntimeError("Gemini Files upload retry budget exhausted")
            upload_budget.claim()
            uf = None
            remote_name = ""
            try:
                uf = await capacity_control.run_heavy_gemini_call(
                    lambda: client.aio.files.upload(
                        file=mp3_path,
                        config=types.UploadFileConfig(
                            mime_type="audio/mpeg",
                            display_name=f"{performer} - {title}",
                        ),
                    ),
                    domain="files",
                )
                remote_name = str(getattr(uf, "name", "") or "")
                _loop = asyncio.get_running_loop()
                _poll_start = _loop.time()
                while uf.state == "PROCESSING":
                    if _loop.time() - _poll_start > _MAX_UPLOAD_WAIT:
                        raise TimeoutError(
                            f"Gemini file processing timeout ({_MAX_UPLOAD_WAIT}s)"
                        )
                    await set_progress(status_msg, 4, {"info": "🧠 Gemini обрабатывает аудио... ⏳"})
                    await asyncio.sleep(3)
                    uf = await capacity_control.run_heavy_gemini_call(
                        lambda _name=uf.name: client.aio.files.get(name=_name),
                        domain="files",
                    )
                if uf.state == "FAILED":
                    raise RuntimeError("Gemini File processing failed")
                return uf, client
            except Exception as exc:
                if is_overload_error(exc):
                    delay = capacity_control.transient_retry_delay(upload_budget.used)
                    capacity_control.note_overload(delay, domain="files")
                if remote_name:
                    await _safe_delete_gemini_file(client, remote_name)
                raise

        await set_progress(status_msg, 4, {"info": "🧠 AI анализирует материал..."})
        duration_str = format_timestamp(duration)
        prompt = build_audio_analysis_prompt(
            title=title,
            performer=performer,
            duration_str=duration_str,
            duration_seconds=duration,
            mode=AUDIO_ANALYSIS_MODE,
        )
        _compacted_prompt = compact_prompt_for_generation(prompt)
        if _compacted_prompt.saved_chars:
            logger.info(
                "Gemini audio prompt compacted %d -> %d chars (removed_lines=%d)",
                _compacted_prompt.original_chars,
                _compacted_prompt.compacted_chars,
                _compacted_prompt.removed_lines,
            )
        prompt = _compacted_prompt.text
        _audio_tokens_est = int(duration or 0) * 32
        logger.info(
            "Gemini audio analysis prompt prepared: mode=%s chars=%s duration=%ss (~%dK audio tokens)",
            AUDIO_ANALYSIS_MODE,
            len(prompt),
            duration,
            _audio_tokens_est // 1000,
        )

        last_err = None
        response = None
        success = False
        _current_model = _models_to_try[0]

        async def _generate_once(client, audio_part, model_name):
            """One logical inference attempt; schema fallback is schema-only."""
            if _audio_structured_output_enabled():
                try:
                    return await capacity_control.run_heavy_gemini_call(
                        lambda: asyncio.wait_for(
                            client.aio.models.generate_content(
                                model=model_name,
                                contents=[audio_part, prompt],
                                config=make_audio_config(
                                    max_output_tokens=65536,
                                    model_name=model_name,
                                    response_mime_type="application/json",
                                    response_schema=audio_analysis_response_schema(),
                                ),
                            ),
                            timeout=_audio_structured_timeout(),
                        ),
                        domain="inference",
                    )
                except Exception as _schema_err:
                    if is_quota_error(_schema_err) or is_overload_error(_schema_err):
                        raise
                    if _is_timeout_error(_schema_err):
                        raise
                    logger.warning(
                        "audio_analysis structured output failed (%s: %s) — retry legacy JSON config",
                        type(_schema_err).__name__, str(_schema_err)[:180],
                    )
            return await capacity_control.run_heavy_gemini_call(
                lambda: asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=model_name,
                        contents=[audio_part, prompt],
                        config=make_audio_config(
                            max_output_tokens=65536,
                            model_name=model_name,
                        ),
                    ),
                    timeout=960.0,
                ),
                domain="inference",
            )

        for _model_idx, _current_model in enumerate(_models_to_try):
            # 429/rate-limit state is project-scoped, not a model-global fact.
            # Do not skip this semantic model for independent configured clients.
            if success:
                break
            _obs_model = _current_model
            _obs_is_fallback = False
            last_err = None
            response = None
            success = False

            for client_index, client in enumerate(GEMINI_CLIENTS, 1):
                if success or inference_budget.exhausted or upload_budget.exhausted:
                    break
                audio_part = used_audio_part if used_client is client else None

                if audio_part is None:
                    try:
                        audio_part, used_client = await upload_to_client(client)
                        used_audio_part = audio_part
                    except Exception as upload_err:
                        last_err = upload_err
                        transient_upload = (
                            is_quota_error(upload_err)
                            or is_overload_error(upload_err)
                            or _is_timeout_error(upload_err)
                        )
                        if not transient_upload:
                            raise
                        logger.warning(
                            "Gemini Files upload transient failure client %d/%d, budget=%d/%d: %s: %s",
                            client_index,
                            len(GEMINI_CLIENTS),
                            upload_budget.used,
                            upload_budget.limit,
                            type(upload_err).__name__,
                            str(upload_err)[:200],
                        )
                        if upload_budget.exhausted:
                            break
                        if is_overload_error(upload_err):
                            await asyncio.sleep(
                                capacity_control.transient_retry_delay(upload_budget.used)
                            )
                        continue

                same_client_transients = 0
                while not inference_budget.exhausted and same_client_transients < 2:
                    inference_budget.claim()
                    _obs_retry_num = max(0, inference_budget.used - 1)
                    try:
                        response = await _generate_once(client, audio_part, _current_model)
                        success = True
                        break
                    except Exception as e:
                        _is_quota = is_quota_error(e)
                        _is_timeout = _is_timeout_error(e) and not _is_quota
                        _is_overload = is_overload_error(e) and not _is_quota
                        if not (_is_quota or _is_timeout or _is_overload):
                            raise

                        last_err = e
                        same_client_transients += 1
                        if _is_quota:
                            # Quota is project/model-level; retrying same key only wastes time.
                            kind = "квота/429"
                        elif _is_timeout:
                            kind = "timeout"
                        else:
                            kind = "503/disconnect"
                        logger.warning(
                            "Gemini %s: %s: %s — inference budget=%d/%d",
                            kind,
                            type(e).__name__,
                            str(e)[:200],
                            inference_budget.used,
                            inference_budget.limit,
                        )

                        if _is_overload:
                            delay = capacity_control.transient_retry_delay(inference_budget.used)
                            capacity_control.note_overload(delay, domain="inference")
                        elif _is_timeout:
                            delay = capacity_control.transient_retry_delay(inference_budget.used)
                        else:
                            delay = 0.0

                        if inference_budget.exhausted:
                            break

                        if same_client_transients == 1 and not _is_quota:
                            logger.info(
                                "Gemini transient recovery: %.1fs then retry на уже загруженном аудио; global attempt %d/%d",
                                delay,
                                inference_budget.used + 1,
                                inference_budget.limit,
                            )
                            if delay:
                                await asyncio.sleep(delay)
                            continue
                        break

                if success:
                    break
                if audio_part is not None and hasattr(audio_part, "name"):
                    _spawn_safe_delete(client, audio_part.name)
                used_audio_part = None
                used_client = None

            if response is None and last_err is not None and is_quota_error(last_err):
                logger.warning(
                    "Gemini audio quota/rate-limit budget exhausted; limits are project-scoped, so the model is not globally banned"
                )

            if (
                response is None
                and last_err is not None
                and not is_quota_error(last_err)
                and is_overload_error(last_err)
            ):
                # second full re-upload circle is disabled for every transient class.
                logger.warning(
                    "Gemini 503 recovery exhausted at global initial+2 budget; "
                    "additional API keys and second full re-upload circle are disabled"
                )

        if response is None:
            _err = last_err or RuntimeError("Все допустимые Gemini-попытки исчерпаны")
            await alog_gemini_run(
                task="audio_analysis",
                video_id=_obs_video_id,
                model=_obs_model,
                thinking_level=_obs_thinking_level,
                duration_ms=_obs_duration_ms(),
                retry_num=_obs_retry_num,
                is_fallback=_obs_is_fallback,
                json_valid=False,
                error=f"{type(_err).__name__}: {_mask_api_key(str(_err))[:300]}",
            )
            raise _err

        try:
            _fin = response.candidates[0].finish_reason if response.candidates else "NO_CANDIDATES"
            logger.info(f"Gemini response: finish_reason={_fin}")
        except Exception:
            pass

        def _extract_raw_text(resp) -> str | None:
            _txt = None
            try:
                _txt = resp.text
            except ValueError:
                pass
            if not _txt and getattr(resp, "candidates", None):
                for part in resp.candidates[0].content.parts:
                    if hasattr(part, "thought") and part.thought:
                        continue
                    if hasattr(part, "text") and part.text:
                        _txt = part.text
                        break
            return _txt

        def _finish_reason_of(resp) -> str:
            if getattr(resp, "candidates", None):
                _f = getattr(resp.candidates[0], "finish_reason", None)
                return str(_f) if _f is not None else "UNKNOWN"
            return "UNKNOWN"

        async def _retry_low_thinking(reason: str):
            """One real LOW-thinking recovery for empty/MAX_TOKENS responses."""
            logger.warning("Gemini %s — повтор с thinking_level=low", reason)
            try:
                if _audio_structured_output_enabled():
                    _cfg = make_audio_config(
                        max_output_tokens=65536,
                        model_name=_obs_model or _current_model,
                        thinking_level="low",
                        response_mime_type="application/json",
                        response_schema=audio_analysis_response_schema(),
                    )
                else:
                    _cfg = make_audio_config(
                        max_output_tokens=65536,
                        model_name=_obs_model or _current_model,
                        thinking_level="low",
                    )
                return await capacity_control.run_heavy_gemini_call(
                    lambda: asyncio.wait_for(
                        used_client.aio.models.generate_content(
                            model=_obs_model or _current_model,
                            contents=[used_audio_part, prompt],
                            config=_cfg,
                        ),
                        timeout=960.0,
                    ),
                    domain="inference",
                )
            except Exception as _rl_err:
                if is_overload_error(_rl_err):
                    capacity_control.note_overload(
                        capacity_control.transient_retry_delay(1),
                        domain="inference",
                    )
                logger.warning("low-thinking retry failed: %s", str(_rl_err)[:200])
                return None

        raw_text = _extract_raw_text(response)
        _finish_str = _finish_reason_of(response)
        if (not raw_text) or ("MAX_TOKENS" in _finish_str):
            _reason = "вернул пустой ответ (thinking-only)" if not raw_text else "обрезал ответ (MAX_TOKENS)"
            _retry_resp = await _retry_low_thinking(_reason)
            if _retry_resp is not None:
                _retry_text = _extract_raw_text(_retry_resp)
                _retry_finish = _finish_reason_of(_retry_resp)
                if _retry_text and "MAX_TOKENS" not in _retry_finish:
                    response = _retry_resp
                    raw_text = _retry_text
                    _finish_str = _retry_finish
                    _obs_thinking_level = "low"
                    logger.info("Gemini low-thinking retry успешен")

        if not raw_text:
            logger.warning("Gemini вернул пустой ответ (thinking-only или safety filter)")
            await alog_gemini_response(
                response=response,
                task="audio_analysis",
                video_id=_obs_video_id,
                model=_obs_model or _current_model,
                thinking_level=_obs_thinking_level,
                duration_ms=_obs_duration_ms(),
                retry_num=_obs_retry_num,
                is_fallback=_obs_is_fallback,
                json_valid=False,
                error="empty_response",
            )
            return None, used_client, used_audio_part
        answer = raw_text.strip()
        logger.info(f"Gemini ответ (первые 2000 символов): {answer[:2000]}")

        if "MAX_TOKENS" in _finish_str:
            logger.error(
                f"Gemini обрезал ответ (MAX_TOKENS) даже после low-thinking retry. "
                f"Длина: {len(answer)} символов. Возвращаем None."
            )
            await alog_gemini_response(
                response=response,
                task="audio_analysis",
                video_id=_obs_video_id,
                model=_obs_model or _current_model,
                thinking_level=_obs_thinking_level,
                duration_ms=_obs_duration_ms(),
                retry_num=_obs_retry_num,
                is_fallback=_obs_is_fallback,
                json_valid=False,
                error="max_tokens",
            )
            return None, used_client, used_audio_part
        elif _finish_str not in ("FinishReason.STOP", "STOP", "1", "UNKNOWN"):
            logger.warning(
                f"Gemini finish_reason={_finish_str} — ответ возможно неполный. "
                f"Длина: {len(answer)} символов"
            )

        parsed = _parse_gemini_response(answer, duration)
        if parsed is not None:
            parsed = _validate_and_fix_timestamps(parsed, duration)
            parsed = await _repair_timestamp_coverage_if_needed(
                used_client, used_audio_part, parsed, duration, _obs_model or _current_model, _obs_video_id
            )

        await alog_gemini_response(
            response=response,
            task="audio_analysis",
            video_id=_obs_video_id,
            model=_obs_model or _current_model,
            thinking_level=_obs_thinking_level,
            duration_ms=_obs_duration_ms(),
            retry_num=_obs_retry_num,
            is_fallback=_obs_is_fallback,
            json_valid=parsed is not None,
            error="" if parsed is not None else "parse_failed",
        )

        return parsed, used_client, used_audio_part

    except Exception as e:
        err_type = type(e).__name__
        safe_err = _mask_api_key(str(e))
        logger.error(f"Ошибка Gemini AI: {err_type}: {safe_err[:300]}")
        await alog_gemini_run(
            task="audio_analysis",
            video_id=_obs_video_id,
            model=_obs_model,
            thinking_level=_obs_thinking_level,
            duration_ms=_obs_duration_ms(),
            retry_num=_obs_retry_num,
            is_fallback=_obs_is_fallback,
            json_valid=False,
            error=f"{err_type}: {safe_err[:300]}",
        )
        if used_audio_part and hasattr(used_audio_part, 'name') and used_client:
            _spawn_safe_delete(used_client, used_audio_part.name)
        return None, None, None
