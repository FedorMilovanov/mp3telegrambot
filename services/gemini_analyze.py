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

BUG-B01 fix: docstring приведён в соответствие с реальным return.
"""
from core.globals import (
    HAS_GEMINI, GEMINI_API_KEY,
    GEMINI_CLIENTS,           # FIX gemini_analyze
    is_quota_error, is_overload_error, is_model_exhausted, mark_model_exhausted,  # FIX gemini_analyze,
    make_audio_config,
)
from core.database import GEMINI_MODEL     # FIX gemini_analyze
from core.json_parser import _parse_gemini_response, _recover_truncated_json  # FIX gemini_analyze
from core.progress import set_progress     # FIX gemini_analyze
from core.utils import format_timestamp, mask_api_key as _mask_api_key  # FIX gemini_analyze
from core.prompts import build_audio_analysis_prompt, AUDIO_ANALYSIS_MODE  # deep prompt builder
from core.observability import alog_gemini_response, alog_gemini_run  # V3 observability
from core.candidate_schema import audio_analysis_response_schema, timestamp_repair_response_schema
from core.prompt_compactor import compact_prompt_for_generation
from core.core_utils import time_to_seconds
from core.text_utils import _scrub_inline

import asyncio
import logging
import re
import time
import os

# types — из google.genai (условный импорт, уже в globals.py)
try:
    from google.genai import types
except ImportError:
    types = None

logger = logging.getLogger(__name__)


def _audio_structured_output_enabled() -> bool:
    """Opt-out flag for primary audio structured JSON output."""
    return (os.getenv("AUDIO_ANALYSIS_STRUCTURED", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}


def _audio_structured_timeout() -> float:
    """Shorter timeout for schema attempt; legacy retry keeps full timeout."""
    try:
        return max(30.0, min(float(os.getenv("AUDIO_STRUCTURED_TIMEOUT", "180")), 600.0))
    except (TypeError, ValueError):
        return 180.0



def _timestamp_repair_enabled() -> bool:
    return (os.getenv("AUDIO_TIMESTAMP_REPAIR", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}


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
    """One targeted, non-fatal repair pass for low timestamp coverage."""
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
        "Не меняй автора, тему и другие поля. Не делай механическую нарезку; ставь таймкоды только на смысловые повороты.\n\n"
        f"Текущий неполный список:\n{current[:4500]}"  # raised from 3000 for better repair context
    )
    try:
        resp = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=model_name,
                contents=[audio_part, prompt],
                config=make_audio_config(
                    max_output_tokens=16000,  # raised for longer timestamp lists
                    model_name=model_name,
                    response_mime_type="application/json",
                    response_schema=timestamp_repair_response_schema(),
                    thinking_level="low",
                ),
            ),
            timeout=float(os.getenv("AUDIO_TIMESTAMP_REPAIR_TIMEOUT", "240")),
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
        logger.warning("Timestamp coverage repair failed non-fatally: %s: %s", type(exc).__name__, str(exc)[:180])
        await alog_gemini_run(
            task="audio_timestamp_repair", video_id=video_id, model=model_name,
            thinking_level="low", json_valid=False, error=f"{type(exc).__name__}: {str(exc)[:240]}",
        )
    return parsed

# BUG-B02: максимальное время ожидания обработки файла Gemini
_MAX_UPLOAD_WAIT = 600  # 10 минут


async def _safe_delete_gemini_file(client, file_name: str) -> None:
    """BUG-B06: безопасное удаление файла Gemini с обработкой ошибок."""
    try:
        await client.aio.files.delete(name=file_name)
    except Exception as e:
        logger.warning(f"Не удалось удалить Gemini file {file_name}: {e}")


# FIX: храним сильные ссылки на fire-and-forget задачи удаления файлов.
# Без этого asyncio может собрать задачу GC до завершения (документированное
# поведение asyncio.create_task), и временный файл в Gemini Files API не удалится.
_BG_DELETE_TASKS: set = set()


def _spawn_safe_delete(client, file_name: str) -> None:
    """Запускает удаление файла Gemini как фоновую задачу с защитой от GC."""
    if not file_name or client is None:
        return
    task = asyncio.create_task(_safe_delete_gemini_file(client, file_name))
    _BG_DELETE_TASKS.add(task)
    task.add_done_callback(_BG_DELETE_TASKS.discard)


async def _gemini_call_with_retry(call_fn, max_attempts: int = 3, backoff: int = 30):
    """BUG-B09: единый retry-хелпер для Gemini generate_content с ReadTimeout."""
    last_err = None
    for attempt in range(max_attempts):
        try:
            return await call_fn()
        except Exception as e:
            _is_read_timeout = (
                "ReadTimeout" in type(e).__name__
                or isinstance(e, asyncio.TimeoutError)
            )
            if _is_read_timeout and attempt < max_attempts - 1:
                wait = backoff * (attempt + 1)
                logger.warning(
                    f"Gemini ReadTimeout, повтор {attempt + 1}/{max_attempts} через {wait}с..."
                )
                await asyncio.sleep(wait)
                last_err = e
                continue
            raise
    raise last_err


def _validate_and_fix_timestamps(data: dict, duration: int) -> dict:
    """BUG-B03: удаляет/обнуляет таймкоды за пределами duration из parsed_data.

    Рекурсивно обходит списки и словари.
    Поддерживает форматы M:SS и H:MM:SS.
    """
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
    """Анализирует аудио через Gemini API.

    Returns:
        (parsed_data, used_client, used_audio_part) — тройка.
        parsed_data: dict или None при ошибке.
        used_client, used_audio_part: для повторного использования файла.
        Все три None при фатальной ошибке.
    """
    # BUG-B07: защита от нулевой длительности
    if not duration or duration <= 0:
        logger.warning("gemini_analyze_audio: duration=0 или не задан, пропускаем анализ.")
        return None, None, None

    if not GEMINI_CLIENTS:
        return None, None, None

    # AUDIT FIX MULTI-MODEL: fallback по качеству (для редких но мощных запросов)
    # Юзкейс: 1-5 видео/день, главное — качество анализа
    _user_model = GEMINI_MODEL
    _fallback_models = [
        _user_model,                # из .env, рекомендуется gemini-3.5-flash
        "gemini-2.5-flash-lite",    # 5 RPM / 20 RPD — свежая модель
        "gemini-3.1-flash-lite",    # 15 RPM / 500 RPD — последний шанс
    ]
    _seen = set()
    _models_to_try = []
    for _m in _fallback_models:
        if _m and _m not in _seen:
            _seen.add(_m)
            _models_to_try.append(_m)
    logger.info(f"Gemini multi-model fallback (приоритет качества): {_models_to_try}")

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
        audio_bytes = None  # AUDIT FIX: всегда upload (inline отключён, чтобы избежать 503 на 20+MB base64)

        async def upload_to_client(client):
            """Загружает файл через конкретный ключ и возвращает (audio_part, client)."""
            if file_size_mb > 0:  # AUDIT FIX: всегда upload (inline ломается на больших base64)
                uf = await client.aio.files.upload(
                    file=mp3_path,
                    config=types.UploadFileConfig(mime_type="audio/mpeg", display_name=f"{performer} - {title}")
                )
                # BUG-B02: таймаут на обработку файла Gemini
                # AUDIT C4: get_event_loop() → get_running_loop() (Python 3.12+ deprecated)
                _loop = asyncio.get_running_loop()
                _poll_start = _loop.time()
                while uf.state == "PROCESSING":
                    if _loop.time() - _poll_start > _MAX_UPLOAD_WAIT:
                        raise TimeoutError(
                            f"Gemini file processing timeout ({_MAX_UPLOAD_WAIT}s)"
                        )
                    await set_progress(status_msg, 4, {"info": "🧠 Gemini обрабатывает аудио... ⏳"})
                    await asyncio.sleep(3)
                    uf = await client.aio.files.get(name=uf.name)
                if uf.state == "FAILED":
                    raise Exception("File processing failed")
                return uf, client
            else:
                return types.Part.from_bytes(data=audio_bytes, mime_type="audio/mpeg"), client

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
        logger.info(
            "Gemini audio analysis prompt prepared: mode=%s chars=%s duration=%ss",
            AUDIO_ANALYSIS_MODE,
            len(prompt),
            duration,
        )

        # AUDIT FIX MULTI-MODEL: внешний цикл по моделям-кандидатам
        last_err = None
        response = None
        success = False
        _current_model = _models_to_try[0]

        for _model_idx, _current_model in enumerate(_models_to_try):
            if is_model_exhausted(_current_model):
                logger.warning("Gemini пропускаю модель %s: quota exhausted in-memory", _current_model)
                continue
            if success:
                break
            _obs_model = _current_model
            _obs_is_fallback = _model_idx > 0
            if _model_idx > 0:
                logger.warning(
                    f"Gemini переключаюсь на резервную модель: {_current_model} "
                    f"(модель #{_model_idx+1}/{len(_models_to_try)})"
                )
            # Для каждого клиента загружаем файл отдельно (файлы не переносятся между ключами)
            last_err = None
            response = None
            success = False
            for client in GEMINI_CLIENTS:
                if success:
                    break
                for attempt in range(3):
                    try:
                        _obs_retry_num = attempt
                        audio_part = None  # инициализируем до upload, чтобы except не поймал NameError
                        audio_part, used_client = await upload_to_client(client)
                        used_audio_part = audio_part

                        # BUG-B09: единый retry через _gemini_call_with_retry
                        # Используем default-args чтобы зафиксировать текущие client/audio_part
                        # и избежать late-binding closure на переменные цикла.
                        async def _do_generate(_c=client, _ap=audio_part, _current_model=_current_model):
                            _use_schema = _audio_structured_output_enabled()
                            if _use_schema:
                                try:
                                    return await asyncio.wait_for(
                                        _c.aio.models.generate_content(
                                            model=_current_model,
                                            contents=[_ap, prompt],
                                            config=make_audio_config(
                                                max_output_tokens=65536,
                                                model_name=_current_model,
                                                response_mime_type="application/json",
                                                response_schema=audio_analysis_response_schema(),
                                            ),
                                        ),
                                        timeout=_audio_structured_timeout(),
                                    )
                                except Exception as _schema_err:
                                    # Quota/overload are not schema problems; let outer fallback
                                    # rotate keys/models normally. For SDK/model schema incompatibility,
                                    # retry the same call with legacy JSON config.
                                    if is_quota_error(_schema_err) or is_overload_error(_schema_err):
                                        raise
                                    logger.warning(
                                        "audio_analysis structured output failed (%s: %s) — retry legacy JSON config",
                                        type(_schema_err).__name__, str(_schema_err)[:180],
                                    )
                            return await asyncio.wait_for(
                                _c.aio.models.generate_content(
                                    model=_current_model,
                                    contents=[_ap, prompt],
                                    config=make_audio_config(max_output_tokens=65536, model_name=_current_model),
                                ),
                                timeout=960.0,
                            )

                        response = await _gemini_call_with_retry(_do_generate, max_attempts=3, backoff=30)
                        success = True
                        break
                    except Exception as e:
                        _is_quota = is_quota_error(e)
                        _is_overload = is_overload_error(e) and not _is_quota
                        if _is_quota or _is_overload:
                            logger.warning(
                                f"Gemini {'квота' if _is_quota else '503/disconnect'}: "
                                f"{type(e).__name__}: {str(e)[:200]} -- пробую следующий ключ..."
                            )
                            # FIX: удаляем загруженный временный файл при ротации
                            # ключа независимо от размера. Раньше порог >20MB оставлял
                            # файлы 0–20MB висеть в Gemini Files API при каждой смене ключа.
                            if audio_part is not None and hasattr(audio_part, 'name'):
                                _spawn_safe_delete(client, audio_part.name)
                            last_err = e
                            # Quota is project/model-level; retrying same key only wastes time.
                            if _is_quota:
                                # Не баним модель глобально (ключи в разных проектах имеют свои квоты)
                                break
                            # AUDIT FIX 503-RETRY: на первых попытках ждём и повторяем тем же ключом
                            if _is_overload and attempt < 2:
                                _wait_503 = 15 * (attempt + 1)  # 15s, 30s
                                logger.info(f"Gemini 503: жду {_wait_503}s и повторяю тем же ключом (попытка {attempt+2}/3)...")
                                await asyncio.sleep(_wait_503)
                                continue  # следующая попытка на ЭТОМ же ключе
                            break  # все попытки на ключе исчерпаны — следующий клиент
                        raise  # неизвестная ошибка — пробрасываем

            # AUDIT FIX 503-RETRY: если все ключи упали с 503, ждём 60s и второй круг
            if response is None and last_err is not None and (not is_quota_error(last_err)) and is_overload_error(last_err):
                logger.warning("Gemini 503 на всех ключах — жду 60s и пробую ещё раз весь круг...")
                await asyncio.sleep(60)
                for client in GEMINI_CLIENTS:
                    try:
                        _obs_retry_num = 3
                        audio_part, used_client = await upload_to_client(client)
                        response = await asyncio.wait_for(
                            client.aio.models.generate_content(
                                model=_current_model,
                                contents=[audio_part, prompt],
                                config=make_audio_config(max_output_tokens=65536, model_name=_current_model),
                            ),
                            timeout=960.0,
                        )
                        used_audio_part = audio_part
                        logger.info("Gemini: второй круг успешен!")
                        break
                    except Exception as e2:
                        logger.warning(f"Gemini второй круг: {type(e2).__name__}: {str(e2)[:150]}")
                        last_err = e2
                        continue

        if response is None:
            _err = last_err or RuntimeError("Все Gemini-клиенты недоступны")
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

        # AUDIT DIAG: логируем финальный finish_reason и длину ответа
        try:
            _fin = response.candidates[0].finish_reason if response.candidates else "NO_CANDIDATES"
            logger.info(f"Gemini response: finish_reason={_fin}")
        except Exception:
            pass

        # BUG-B04: response.text может быть None (thinking-only) или ValueError (safety filter)
        raw_text = None
        try:
            raw_text = response.text
        except ValueError:
            pass
        if not raw_text:
            # Fallback: собираем text из parts, пропуская thinking-части
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, "thought") and part.thought:
                        continue
                    if hasattr(part, "text") and part.text:
                        raw_text = part.text
                        break
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

        # BUG-B05: явная обработка MAX_TOKENS — обрезанный ответ = None
        if response.candidates:
            _finish = getattr(response.candidates[0], "finish_reason", None)
            _finish_str = str(_finish) if _finish is not None else "UNKNOWN"
            if "MAX_TOKENS" in _finish_str:
                logger.error(
                    f"Gemini обрезал ответ (MAX_TOKENS). Длина: {len(answer)} символов. "
                    "Возвращаем None."
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
            elif _finish_str not in ("FinishReason.STOP", "STOP", "1"):
                logger.warning(
                    f"Gemini finish_reason={_finish_str} — ответ возможно неполный. "
                    f"Длина: {len(answer)} символов"
                )

        # BUG-B03: валидация и исправление таймкодов после парсинга
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

        # Файл НЕ удаляем здесь — нужен для create_telegraph_synopsis
        return parsed, used_client, used_audio_part

    except Exception as e:
        # BUG-B08: маскируем API-ключи, урезаем до 300 символов
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
            # FIX: GC-safe фоновое удаление (ссылка хранится в _BG_DELETE_TASKS)
            _spawn_safe_delete(used_client, used_audio_part.name)
        return None, None, None
