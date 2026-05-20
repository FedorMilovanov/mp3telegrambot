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
    is_quota_error, is_overload_error,  # FIX gemini_analyze,
    make_audio_config,
)
from core.database import GEMINI_MODEL     # FIX gemini_analyze
from core.json_parser import _parse_gemini_response  # FIX gemini_analyze
from core.progress import set_progress     # FIX gemini_analyze
from core.utils import format_timestamp, mask_api_key as _mask_api_key  # FIX gemini_analyze
from core.prompts import build_audio_analysis_prompt, AUDIO_ANALYSIS_MODE  # deep prompt builder

import asyncio
import logging
import re

# types — из google.genai (условный импорт, уже в globals.py)
try:
    from google.genai import types
except ImportError:
    types = None

logger = logging.getLogger(__name__)

# BUG-B02: максимальное время ожидания обработки файла Gemini
_MAX_UPLOAD_WAIT = 600  # 10 минут


async def _safe_delete_gemini_file(client, file_name: str) -> None:
    """BUG-B06: безопасное удаление файла Gemini с обработкой ошибок."""
    try:
        await client.aio.files.delete(name=file_name)
    except Exception as e:
        logger.warning(f"Не удалось удалить Gemini file {file_name}: {e}")


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
        "gemini-3.1-flash-lite", "gemini-2.5-flash-lite",  # FINAL-POLISH FIX 1            # 5 RPM / 20 RPD — тоже свежая 3.x
        "gemini-3.1-flash-lite",     # 15 RPM / 500 RPD — последний шанс, точно прорвётся
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

    try:
        file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
        await set_progress(status_msg, 4, {"info": "🧠 Загружаю аудио для анализа..."})
        loop = asyncio.get_running_loop()
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
            if success:
                break
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
                        audio_part = None  # инициализируем до upload, чтобы except не поймал NameError
                        audio_part, used_client = await upload_to_client(client)
                        used_audio_part = audio_part

                        # BUG-B09: единый retry через _gemini_call_with_retry
                        # Используем default-args чтобы зафиксировать текущие client/audio_part
                        # и избежать late-binding closure на переменные цикла.
                        async def _do_generate(_c=client, _ap=audio_part):
                            return await asyncio.wait_for(
                                _c.aio.models.generate_content(
                                    model=GEMINI_MODEL,
                                    contents=[_ap, prompt],
                                    config=make_audio_config(temperature=0.1, max_output_tokens=65536),
                                ),
                                timeout=960.0,
                            )

                        response = await _gemini_call_with_retry(_do_generate, max_attempts=3, backoff=30)
                        success = True
                        break
                    except Exception as e:
                        if is_quota_error(e) or is_overload_error(e):
                            logger.warning(f"Gemini {'квота' if is_quota_error(e) else '503/disconnect'}: {type(e).__name__}: {str(e)[:200]} -- пробую следующий ключ...")
                            if file_size_mb > 20 and audio_part is not None and hasattr(audio_part, 'name'):
                                # BUG-B06: безопасное удаление через хелпер
                                asyncio.create_task(_safe_delete_gemini_file(client, audio_part.name))
                            last_err = e
                            # AUDIT FIX 503-RETRY: на первых попытках ждём и повторяем тем же ключом
                            if is_overload_error(e) and attempt < 2:
                                _wait_503 = 15 * (attempt + 1)  # 15s, 30s
                                logger.info(f"Gemini 503: жду {_wait_503}s и повторяю тем же ключом (попытка {attempt+2}/3)...")
                                await asyncio.sleep(_wait_503)
                                continue  # следующая попытка на ЭТОМ же ключе
                            break  # все попытки на ключе исчерпаны — следующий клиент
                        raise  # неизвестная ошибка — пробрасываем

            # AUDIT FIX 503-RETRY: если все ключи упали с 503, ждём 60s и второй круг
            if response is None and last_err is not None and is_overload_error(last_err):
                logger.warning("Gemini 503 на всех ключах — жду 60s и пробую ещё раз весь круг...")
                await asyncio.sleep(60)
                for client in GEMINI_CLIENTS:
                    try:
                        audio_part, used_client = await upload_to_client(client)
                        response = await asyncio.wait_for(
                            client.aio.models.generate_content(
                                model=GEMINI_MODEL,
                                contents=[audio_part, prompt],
                                config=make_audio_config(temperature=0.1, max_output_tokens=65536),
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
            raise last_err or RuntimeError("Все Gemini-клиенты недоступны")

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

        # Файл НЕ удаляем здесь — нужен для create_telegraph_synopsis
        return parsed, used_client, used_audio_part

    except Exception as e:
        # BUG-B08: маскируем API-ключи, урезаем до 300 символов
        err_type = type(e).__name__
        safe_err = _mask_api_key(str(e))
        logger.error(f"Ошибка Gemini AI: {err_type}: {safe_err[:300]}")
        if used_audio_part and hasattr(used_audio_part, 'name') and used_client:
            # BUG-B06: безопасное удаление через хелпер
            asyncio.create_task(_safe_delete_gemini_file(used_client, used_audio_part.name))
        return None, None, None
