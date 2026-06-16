#!/usr/bin/env python3
"""
Shorts Candidates — create_shorts_candidates, create_clips_candidates.
Извлечено из bot.py строки 8942–9163, 10907–11168.
"""
from core.globals import (
    HAS_GEMINI, GEMINI_API_KEY,
    GEMINI_CLIENTS, gemini_generate,    # FIX shorts_candidates,
    make_audio_config,
)
from core.database import GEMINI_MODEL       # FIX shorts_candidates
from core.json_parser import (
    time_to_seconds,
    _recover_truncated_json,            # FIX shorts_candidates
)
from core.text_utils import (
    _clean_field, _scrub_inline, _strip_meta_lines,  # FIX shorts_candidates
    normalize_hashtag,
)
from core.utils import format_timestamp      # FIX shorts_candidates
from core.prompts import (
    SHORTS_PROMPT, EXTRAS_PROMPT, CLIPS_PROMPT, CLIPS_SERMON_PROMPT,
    CLIPS_MIN_SEC, CLIPS_MAX_SEC, CLIPS_IDEAL_MAX_SEC,  # FIX shorts_candidates
)
from core.candidate_schema import (
    CandidateValidationReport, clips_response_schema, shorts_response_schema,
    structured_json_config_kwargs, validate_candidate_times,
)
from core.observability import alog_gemini_response, alog_gemini_run

import asyncio
import json      # FIX shorts_candidates
import logging
import re
import time
from pathlib import Path  # FIX shorts_candidates

# types — из google.genai
try:
    from google.genai import types
except ImportError:
    types = None

logger = logging.getLogger(__name__)

# ── BUG-D01: валидация клипа ──────────────────────────────────────────────
def _valid_clip(clip: dict, duration: int) -> bool:
    """Проверяет что клип имеет валидные start/end в пределах duration."""
    try:
        s = float(clip["start_seconds"])
        e = float(clip["end_seconds"])
        return 0 <= s < e and (duration <= 0 or e <= duration)
    except (KeyError, TypeError, ValueError):
        return False


# ── BUG-D02: удаление overlap клипов (для shorts) ────────────────────────
def _remove_overlapping_shorts(clips: list[dict]) -> list[dict]:
    """Удаляет клипы с >10% пересечения с ранее принятыми (по start_seconds)."""
    if not clips:
        return clips
    clips = sorted(clips, key=lambda c: c.get("start_seconds", 0))
    result = [clips[0]]
    for clip in clips[1:]:
        c_start = clip.get("start_seconds", 0)
        c_end   = clip.get("end_seconds", 0)
        c_len   = max(1, c_end - c_start)
        overlaps = False
        for accepted in result:
            a_start = accepted.get("start_seconds", 0)
            a_end   = accepted.get("end_seconds", 0)
            a_len   = max(1, a_end - a_start)
            overlap = max(0, min(c_end, a_end) - max(c_start, a_start))
            if overlap / min(c_len, a_len) > 0.1:
                overlaps = True
                break
        if not overlaps:
            result.append(clip)
    return result


# ── BUG-D03: нормализация хэштегов в CamelCase ───────────────────────────
# Каноническая реализация перенесена в core/text_utils.normalize_hashtag (DRY).
# Алиас сохранён для обратной совместимости внутри модуля.
_normalize_hashtag = normalize_hashtag


def _candidate_audio_config(max_output_tokens: int, schema: dict | None = None):
    return make_audio_config(
        max_output_tokens=max_output_tokens,
        thinking_level="low",
        **structured_json_config_kwargs(schema),
    )


async def _generate_audio_candidate_content(
    client, *, model: str, contents: list, max_output_tokens: int, schema: dict | None, task: str
):
    """Try Gemini structured JSON first, then fall back to legacy JSON prompt mode."""
    try:
        return await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=_candidate_audio_config(max_output_tokens, schema),
        )
    except Exception as exc:
        if not schema:
            raise
        logger.warning(
            "%s: structured output failed (%s: %s) — retry legacy JSON config",
            task, type(exc).__name__, str(exc)[:180],
        )
        return await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=_candidate_audio_config(max_output_tokens, None),
        )


async def create_shorts_candidates(
    mp3_path: Path,
    ai_data: dict,
    title: str,
    performer: str,
    duration: int,
    status_msg=None,
    prefix: str = "",
    existing_audio_part=None,
    existing_client=None,
    speed: float = 1.0,
) -> list[dict]:
    """
    Ищет 3–5 кандидатов для Shorts через Gemini.
    Возвращает список словарей:
    [{"start","end","title","reason","kind","start_seconds","end_seconds","duration_seconds"}]
    speed — текущее ускорение; максимальная длина исходного клипа масштабируется.
    """
    if not GEMINI_CLIENTS or not HAS_GEMINI:
        return []

    _obs_started = time.perf_counter()

    def _obs_ms() -> int:
        return int((time.perf_counter() - _obs_started) * 1000)

    try:
        format_name      = (ai_data or {}).get("format", "other") or "other"
        real_author      = (ai_data or {}).get("real_author", "") or performer or ""
        real_event       = (ai_data or {}).get("real_event", "") or ""
        analysis_summary = (ai_data or {}).get("analysis_summary", "") or ""
        argument_arc     = (ai_data or {}).get("argument_arc", "") or ""
        key_categories   = "; ".join((ai_data or {}).get("key_categories", []) or [])
        timestamps       = (ai_data or {}).get("timestamps", "") or ""
        questions        = "\n".join((ai_data or {}).get("questions", []) or [])

        # При ускорении максимальная длина исходника масштабируется:
        # итоговый шортс = исходник / speed → можно брать исходник * speed
        # Цель: итоговый шортс до 3 мин (180s) при любой скорости
        _use_speed   = abs(speed - 1.0) > 0.01
        _shorts_max  = round(180 * speed)   # исходник → итоговые 180s
        _ideal_max   = round(120 * speed)   # желательный максимум
        _shorts_min  = 35                   # минимум не масштабируем

        _structured_schema = shorts_response_schema()

        prompt = SHORTS_PROMPT.format(
            title=title,
            duration=format_timestamp(duration),
            format_name=format_name,
            real_author=real_author,
            real_event=real_event,
            analysis_summary=analysis_summary[:800],
            argument_arc=argument_arc[:600],
            key_categories=key_categories,
            timestamps=timestamps[:1000],
            questions_block=f"- questions:\n{questions}\n" if questions.strip() else "",
            speed_limits=(
                f"\n\nУСКОРЕНИЕ: видео будет ускорено в {speed}x раз. "
                f"Выбирай фрагменты до {_shorts_max} секунд — после ускорения это станет {round(_shorts_max/speed)}s. "
                f"Желательный диапазон исходника: 35–{_ideal_max} секунд."
            ) if _use_speed else "",
        )

        if status_msg:
            try:
                await status_msg.edit_text(f"{prefix}✂️ Ищу лучшие фрагменты для Shorts...")
            except Exception:
                pass

        file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
        response = None

        if existing_audio_part is not None and existing_client is not None:
            try:
                response = await _generate_audio_candidate_content(
                    existing_client, model=GEMINI_MODEL, contents=[existing_audio_part, prompt],
                    max_output_tokens=16000, schema=_structured_schema, task="shorts_candidates",
                )
            except Exception as e:
                logger.warning(f"Shorts candidates existing_audio_part failed: {e}")
                response = None

        if response is None:
            audio_bytes = mp3_path.read_bytes() if file_size_mb <= 20 else None

            async def _upload(client):
                if file_size_mb > 20:
                    uf = await client.aio.files.upload(
                        file=mp3_path,
                        config=types.UploadFileConfig(
                            mime_type="audio/mpeg",
                            display_name=f"{performer} - {title}",
                        ),
                    )
                    _uf_start = asyncio.get_running_loop().time()
                    while uf.state == "PROCESSING":
                        if asyncio.get_running_loop().time() - _uf_start > 300:
                            raise TimeoutError("Gemini file processing timeout (300s)")
                        await asyncio.sleep(3)
                        uf = await client.aio.files.get(name=uf.name)
                    if uf.state == "FAILED":  # fix #8
                        raise Exception("Gemini file processing failed")
                    return uf
                return types.Part.from_bytes(data=audio_bytes, mime_type="audio/mpeg")

            async def _shorts_with_client(client):
                audio_part = await _upload(client)
                _uploaded = getattr(audio_part, "name", None) is not None
                try:
                    return await _generate_audio_candidate_content(
                        client, model=GEMINI_MODEL, contents=[audio_part, prompt],
                        max_output_tokens=16000, schema=_structured_schema, task="shorts_candidates",
                    )
                finally:
                    if _uploaded:
                        try:
                            await client.aio.files.delete(name=audio_part.name)
                        except Exception:
                            pass

            async def _call_shorts(client):
                return await _shorts_with_client(client)

            response = await gemini_generate(GEMINI_CLIENTS, _call_shorts)

        raw_text = ""
        try:
            raw_text = response.text or ""
        except Exception:
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if not getattr(part, "thought", False):
                        raw_text += part.text or ""

        if not raw_text.strip():
            logger.warning("Shorts candidates: Gemini вернул пустой ответ")
            await alog_gemini_response(
                response=response, task="shorts_candidates", video_id=mp3_path.stem,
                model=GEMINI_MODEL, thinking_level="low", duration_ms=_obs_ms(),
                json_valid=False, error="empty_response",
            )
            return []

        clean = raw_text.strip()
        clean = re.sub(r"^```[a-z]*\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean).strip()
        start = clean.find("{")
        end   = clean.rfind("}")
        if start == -1 or end <= start:
            logger.warning(f"Shorts candidates: JSON не найден | текст: {raw_text[:500]}")
            await alog_gemini_response(
                response=response, task="shorts_candidates", video_id=mp3_path.stem,
                model=GEMINI_MODEL, thinking_level="low", duration_ms=_obs_ms(),
                json_valid=False, error="json_not_found",
            )
            return []

        try:
            data = json.loads(clean[start:end + 1])
        except json.JSONDecodeError as e:
            logger.warning(f"Shorts candidates JSONDecodeError: {e} | текст: {raw_text[:500]}")
            # Попытка восстановить обрезанный JSON (MAX_TOKENS от Gemini)
            _recovered = _recover_truncated_json(clean[start:])
            if _recovered is not None:
                logger.info("Shorts candidates: JSON восстановлен (обрезанный ответ)")
                data = _recovered
            else:
                await alog_gemini_response(
                    response=response, task="shorts_candidates", video_id=mp3_path.stem,
                    model=GEMINI_MODEL, thinking_level="low", duration_ms=_obs_ms(),
                    json_valid=False, error="json_decode_error",
                )
                return []

        candidates_raw = data.get("shorts_candidates", [])
        if not isinstance(candidates_raw, list):
            await alog_gemini_response(
                response=response, task="shorts_candidates", video_id=mp3_path.stem,
                model=GEMINI_MODEL, thinking_level="low", duration_ms=_obs_ms(),
                json_valid=False, error="candidates_not_list",
            )
            return []

        report = CandidateValidationReport()
        out: list[dict] = []
        seen_ranges: set[tuple[int, int]] = set()

        for item in candidates_raw[:7]:
            if not isinstance(item, dict):
                continue
            start_t    = str(item.get("start", "")).strip()
            end_t      = str(item.get("end",   "")).strip()
            clip_title = _scrub_inline(_strip_meta_lines(_clean_field(item.get("title",  ""))))
            hook       = _scrub_inline(_strip_meta_lines(_clean_field(item.get("hook",   ""))))
            reason     = _scrub_inline(_strip_meta_lines(_clean_field(item.get("reason", ""))))
            kind       = str(item.get("kind", "")).strip()
            # hashtags: список строк без #
            raw_tags   = item.get("hashtags", [])
            if isinstance(raw_tags, list):
                hashtags = [
                    _normalize_hashtag(t)
                    for t in raw_tags
                    if str(t).strip()
                ][:4]
                hashtags = [h for h in hashtags if h]  # убираем пустые после нормализации
            else:
                hashtags = []

            if not start_t or not end_t or not clip_title:
                continue

            ok_time, reject_reason, start_s, end_s, clip_len = validate_candidate_times(
                start_t, end_t, duration=duration, min_sec=_shorts_min, max_sec=_shorts_max,
                time_to_seconds=time_to_seconds,
            )
            if not ok_time:
                report.reject(reject_reason)
                continue

            key = (start_s, end_s)
            if key in seen_ranges:
                continue
            seen_ranges.add(key)

            report.accept()
            out.append({
                "start":            start_t,
                "end":              end_t,
                "title":            clip_title[:120],
                "hook":             hook[:120],
                "reason":           reason[:220],
                "kind":             kind[:40],
                "hashtags":         hashtags,
                "start_seconds":    start_s,
                "end_seconds":      end_s,
                "duration_seconds": clip_len,
            })

        logger.info(
            f"Shorts candidates: найдено {len(out)} из {len(candidates_raw)} предложенных; "
            f"validation: {report.summary()}"
        )
        # BUG-D02: убираем overlap-клипы (>10% пересечения)
        out = _remove_overlapping_shorts(out)
        await alog_gemini_response(
            response=response, task="shorts_candidates", video_id=mp3_path.stem,
            model=GEMINI_MODEL, thinking_level="low", duration_ms=_obs_ms(),
            json_valid=True, postprocess_fixes=report.rejected, validation_summary=report.to_json(),
        )
        return out[:5]

    except Exception as e:
        logger.warning(f"create_shorts_candidates error: {type(e).__name__}: {e}")
        await alog_gemini_run(
            task="shorts_candidates", video_id=mp3_path.stem, model=GEMINI_MODEL,
            thinking_level="low", duration_ms=_obs_ms(), json_valid=False,
            error=f"{type(e).__name__}: {str(e)[:300]}",
        )
        return []



async def create_clips_candidates(
    mp3_path: Path,
    ai_data: dict,
    title: str,
    performer: str,
    duration: int,
    existing_audio_part=None,
    existing_client=None,
) -> list[dict]:
    """
    Ищет 1–3 длинных clip-кандидата через Gemini.
    Возвращает список словарей:
    [{"start","end","title","reason","kind","hashtags",
      "start_seconds","end_seconds","duration_seconds"}]

    Использует уже загруженный audio_part если доступен (без повторной загрузки).
    Работает независимо от Shorts. Не затрагивает MP3-пайплайн.
    """
    if not GEMINI_CLIENTS or not HAS_GEMINI:
        return []

    _obs_started = time.perf_counter()

    def _obs_ms() -> int:
        return int((time.perf_counter() - _obs_started) * 1000)

    try:
        format_name      = (ai_data or {}).get("format", "other") or "other"
        real_author      = (ai_data or {}).get("real_author", "") or performer or ""
        real_event       = (ai_data or {}).get("real_event", "") or ""
        analysis_summary = (ai_data or {}).get("analysis_summary", "") or ""
        argument_arc     = (ai_data or {}).get("argument_arc", "") or ""
        key_categories   = "; ".join((ai_data or {}).get("key_categories", []) or [])
        timestamps       = (ai_data or {}).get("timestamps", "") or ""
        questions        = "\n".join((ai_data or {}).get("questions", []) or [])

        _qa_formats = ("qa", "interview", "discussion")
        if format_name in _qa_formats:
            # Q&A / интервью / дискуссия — промпт заточен под вопрос/ответ
            prompt = CLIPS_PROMPT.format(
                title=title,
                duration=format_timestamp(duration),
                format_name=format_name,
                real_author=real_author,
                real_event=real_event,
                analysis_summary=analysis_summary[:900],
                argument_arc=argument_arc[:700],
                key_categories=key_categories,
                timestamps=timestamps[:1500],
                questions=questions[:1000] if questions.strip() else "(нет данных)",
            )
            logger.info(f"Clips: используем QA-промпт (format={format_name})")
        else:
            # Проповедь / лекция / другое — промпт заточен под смысловые нервы
            prompt = CLIPS_SERMON_PROMPT.format(
                title=title,
                duration=format_timestamp(duration),
                format_name=format_name,
                real_author=real_author,
                real_event=real_event,
                analysis_summary=analysis_summary[:900],
                argument_arc=argument_arc[:700],
                key_categories=key_categories,
                timestamps=timestamps[:1500],
            )
            logger.info(f"Clips: используем sermon-промпт (format={format_name})")

        _structured_schema = clips_response_schema()

        file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
        response = None

        # Используем уже загруженный audio_part (переиспользование из основного пайплайна)
        if existing_audio_part is not None and existing_client is not None:
            try:
                response = await _generate_audio_candidate_content(
                    existing_client, model=GEMINI_MODEL, contents=[existing_audio_part, prompt],
                    max_output_tokens=12000, schema=_structured_schema, task="clips_candidates",
                )
            except Exception as e:
                logger.warning(f"Clips candidates existing_audio_part failed: {e}")
                response = None

        if response is None:
            audio_bytes = mp3_path.read_bytes() if file_size_mb <= 20 else None

            async def _upload(client):
                if file_size_mb > 20:
                    uf = await client.aio.files.upload(
                        file=mp3_path,
                        config=types.UploadFileConfig(
                            mime_type="audio/mpeg",
                            display_name=f"{performer} - {title}",
                        ),
                    )
                    _uf_start = asyncio.get_running_loop().time()
                    while uf.state == "PROCESSING":
                        if asyncio.get_running_loop().time() - _uf_start > 300:
                            raise TimeoutError("Gemini file processing timeout (300s)")
                        await asyncio.sleep(3)
                        uf = await client.aio.files.get(name=uf.name)
                    if uf.state == "FAILED":  # fix #8
                        raise Exception("Gemini file processing failed")
                    return uf
                return types.Part.from_bytes(data=audio_bytes, mime_type="audio/mpeg")

            async def _clips_with_client(client):
                audio_part = await _upload(client)
                _uploaded = getattr(audio_part, "name", None) is not None
                try:
                    return await _generate_audio_candidate_content(
                        client, model=GEMINI_MODEL, contents=[audio_part, prompt],
                        max_output_tokens=12000, schema=_structured_schema, task="clips_candidates",
                    )
                finally:
                    if _uploaded:
                        try:
                            await client.aio.files.delete(name=audio_part.name)
                        except Exception:
                            pass

            async def _call_clips(client):
                return await _clips_with_client(client)

            response = await gemini_generate(GEMINI_CLIENTS, _call_clips)

        raw_text = ""
        try:
            raw_text = response.text or ""
        except Exception:
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if not getattr(part, "thought", False):
                        raw_text += part.text or ""

        if not raw_text.strip():
            logger.warning("Clips candidates: Gemini вернул пустой ответ")
            await alog_gemini_response(
                response=response, task="clips_candidates", video_id=mp3_path.stem,
                model=GEMINI_MODEL, thinking_level="low", duration_ms=_obs_ms(),
                json_valid=False, error="empty_response",
            )
            return []

        clean = raw_text.strip()
        clean = re.sub(r"^```[a-z]*\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean).strip()
        start = clean.find("{")
        end   = clean.rfind("}")
        if start == -1 or end <= start:
            logger.warning(f"Clips candidates: JSON не найден | текст: {raw_text[:500]}")
            await alog_gemini_response(
                response=response, task="clips_candidates", video_id=mp3_path.stem,
                model=GEMINI_MODEL, thinking_level="low", duration_ms=_obs_ms(),
                json_valid=False, error="json_not_found",
            )
            return []

        try:
            data = json.loads(clean[start:end + 1])
        except json.JSONDecodeError as e:
            logger.warning(f"Clips candidates JSONDecodeError: {e} | текст: {raw_text[:500]}")
            # Попытка восстановить обрезанный JSON (MAX_TOKENS от Gemini)
            _recovered = _recover_truncated_json(clean[start:])
            if _recovered is not None:
                logger.info("Clips candidates: JSON восстановлен (обрезанный ответ)")
                data = _recovered
            else:
                await alog_gemini_response(
                    response=response, task="clips_candidates", video_id=mp3_path.stem,
                    model=GEMINI_MODEL, thinking_level="low", duration_ms=_obs_ms(),
                    json_valid=False, error="json_decode_error",
                )
                return []

        candidates_raw = data.get("clip_candidates", [])
        if not isinstance(candidates_raw, list):
            await alog_gemini_response(
                response=response, task="clips_candidates", video_id=mp3_path.stem,
                model=GEMINI_MODEL, thinking_level="low", duration_ms=_obs_ms(),
                json_valid=False, error="candidates_not_list",
            )
            return []

        report = CandidateValidationReport()
        out: list[dict] = []
        seen_ranges: set[tuple[int, int]] = set()

        for item in candidates_raw[:5]:
            if not isinstance(item, dict):
                continue
            start_t    = str(item.get("start", "")).strip()
            end_t      = str(item.get("end",   "")).strip()
            clip_title = _scrub_inline(_strip_meta_lines(_clean_field(item.get("title",  ""))))
            reason     = _scrub_inline(_strip_meta_lines(_clean_field(item.get("reason", ""))))
            kind       = str(item.get("kind", "")).strip()
            raw_tags   = item.get("hashtags", [])
            if isinstance(raw_tags, list):
                hashtags = [
                    _normalize_hashtag(t)
                    for t in raw_tags if str(t).strip()
                ][:4]
                hashtags = [h for h in hashtags if h]
            else:
                hashtags = []

            if not start_t or not end_t or not clip_title:
                continue

            ok_time, reject_reason, start_s, end_s, clip_len = validate_candidate_times(
                start_t, end_t, duration=duration, min_sec=CLIPS_MIN_SEC, max_sec=CLIPS_MAX_SEC,
                time_to_seconds=time_to_seconds,
            )
            if not ok_time:
                report.reject(reject_reason)
                logger.info(f"Clips: пропускаю '{clip_title}' — {reject_reason}")
                continue

            # Мягкая эвристика против раздутых клипов:
            # если clip превышает желательный максимум (12 мин), логируем предупреждение.
            # Сам clip не отбрасываем — модель могла дать обоснованный длинный фрагмент,
            # но в логах будет видно для мониторинга качества.
            if clip_len > CLIPS_IDEAL_MAX_SEC:
                overshoot_sec = clip_len - CLIPS_IDEAL_MAX_SEC
                logger.info(
                    f"Clips: '{clip_title}' превышает желательный максимум "
                    f"на {overshoot_sec:.0f}s ({clip_len:.0f}s > {CLIPS_IDEAL_MAX_SEC}s) — "
                    f"принят, но проверь плотность"
                )

            # Детекция пересечений: пропускаем кандидатов с >50% overlap с уже принятыми.
            # Exact-match (start_s, end_s) тоже блокируется через seen_ranges.
            overlaps_existing = False
            for (ex_start, ex_end) in seen_ranges:
                overlap_start = max(start_s, ex_start)
                overlap_end   = min(end_s, ex_end)
                overlap_len   = max(0, overlap_end - overlap_start)
                if overlap_len > 0:
                    overlap_ratio = overlap_len / min(clip_len, ex_end - ex_start)
                    if overlap_ratio > 0.5:
                        logger.info(
                            f"Clips: пропускаю '{clip_title}' — "
                            f"{overlap_ratio:.0%} пересечения с уже принятым клипом"
                        )
                        overlaps_existing = True
                        break
            if overlaps_existing:
                continue

            seen_ranges.add((start_s, end_s))
            report.accept()
            out.append({
                "start":            start_t,
                "end":              end_t,
                "title":            clip_title[:140],
                "reason":           reason[:280],
                "kind":             kind[:40],
                "hashtags":         hashtags,
                "start_seconds":    start_s,
                "end_seconds":      end_s,
                "duration_seconds": clip_len,
            })

        _durations = [f"{c['duration_seconds']:.0f}s" for c in out]
        logger.info(
            f"Clips candidates: принято {len(out)} из {len(candidates_raw)} предложенных "
            f"(длины: {_durations}); validation: {report.summary()}"
        )
        await alog_gemini_response(
            response=response, task="clips_candidates", video_id=mp3_path.stem,
            model=GEMINI_MODEL, thinking_level="low", duration_ms=_obs_ms(),
            json_valid=True, postprocess_fixes=report.rejected, validation_summary=report.to_json(),
        )
        return out[:3]

    except Exception as e:
        logger.warning(f"create_clips_candidates error: {type(e).__name__}: {e}")
        await alog_gemini_run(
            task="clips_candidates", video_id=mp3_path.stem, model=GEMINI_MODEL,
            thinking_level="low", duration_ms=_obs_ms(), json_valid=False,
            error=f"{type(e).__name__}: {str(e)[:300]}",
        )
        return []


