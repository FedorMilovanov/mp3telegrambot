#!/usr/bin/env python3
"""
Clips & Montage rendering — render_clip, create_clip_snapshot,
build_clip_caption, render_montage_short, create_extras_candidates.
Извлечено из bot.py строки 11169–11354, 11921–12239.
"""
from core.globals import (
    DOWNLOAD_DIR, THUMBS_DIR, make_text_config_smart,
    GEMINI_CLIENTS, HAS_GEMINI,        # FIX render
    gemini_generate,                    # FIX render
)
from core.database import GEMINI_MODEL       # FIX render
from services.ffmpeg import _get_video_encoder
from services.ffmpeg import _find_silence_end    # FIX render
from services.ffmpeg import _is_static_video     # AUDIT R28
from services.async_process import run_cancellable_process
from services.async_worker import await_owned_coroutine
from core.utils import cleanup_files, format_timestamp
from services.shorts_video import _build_links_block  # FIX render
from core.text_utils import _clean_field, title_case_fragment  # FIX render
from core.core_utils import time_to_seconds                      # FIX: moved to core_utils (был в json_parser)
from core.json_parser import _recover_truncated_json                # V3-P0: partial JSON recovery
from core.observability import alog_gemini_response, alog_gemini_run
from core.candidate_schema import extras_response_schema, structured_json_config_kwargs
from core.prompts import EXTRAS_PROMPT                          # FIX render

import asyncio
import json       # FIX render
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path

# types — из google.genai
try:
    from google.genai import types
except ImportError:
    types = None

logger = logging.getLogger(__name__)

async def render_clip(
    source_video_path: Path,
    output_path: Path,
    start_seconds: int,
    end_seconds: int,
) -> bool:
    """
    Вырезает длинный clip из исходного видео через ffmpeg.
    Сохраняет оригинальное соотношение сторон (16:9 или как есть).
    Без вертикальной трансформации — clips не для Shorts.
    Возвращает True при успехе.
    """
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("render_clip: ffmpeg не найден")
            return False
        if not source_video_path.exists():
            logger.warning(f"render_clip: исходный файл не найден: {source_video_path}")
            return False
        if end_seconds <= start_seconds:
            logger.warning(f"render_clip: невалидный диапазон {start_seconds}..{end_seconds}")
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Корректируем точку конца до ближайшей паузы (8s window for better snap)
        adjusted_end = await _find_silence_end(source_video_path, float(end_seconds), search_window=8.0)
        # Guard: silence snap must not shrink clip below 10s or beyond +12s
        min_end = start_seconds + max(10, int((end_seconds - start_seconds) * 0.5))
        max_end = end_seconds + 12
        if min_end < adjusted_end <= max_end and abs(adjusted_end - end_seconds) > 0.1:
            logger.info(f"Clip end adjusted: {end_seconds}s → {adjusted_end:.1f}s (silence snap)")
            end_seconds = int(round(adjusted_end))
        clip_duration = end_seconds - start_seconds
        if clip_duration <= 0:
            logger.warning("render_clip: clip_duration ≤ 0 после коррекции паузы")
            return False

        _enc, _quality, _preset = await await_owned_coroutine(
            asyncio.to_thread(_get_video_encoder)
        )
        # Clips: чуть лучше качество чем у shorts — cq/crf 22 вместо 23
        _quality_clip = ["-rc", "vbr", "-cq", "22"] if _enc == "h264_nvenc" else ["-crf", "22"]
        _hwaccel = []  # hwaccel cuda убран: CPU-фильтры несовместимы с CUDA decode
        cmd = [
            ffmpeg,
            *_hwaccel,
            "-ss", str(start_seconds),
            "-i", str(source_video_path),
            "-t", str(clip_duration),
            "-c:v", _enc,
            *_preset,
            *_quality_clip,
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-y",
            str(output_path),
        ]

        # AUDIT R29: сериализуем GPU-рендер клипа — именно этот вызов упирался
        # в 15-мин таймаут, когда три видео жарили h264_nvenc одновременно.
        from core.resource_scheduler import scheduler as _sched
        async with _sched.gpu_render:
            proc = await run_cancellable_process(cmd, timeout=900, text=True)
        if proc.returncode != 0:
            stderr_tail = (proc.stderr or '')[-800:]
            # ffmpeg на Windows иногда завершается с кодом != 0 после "received signal 2"
            # (SIGINT от родительского процесса), но файл при этом уже записан корректно.
            # Если файл существует и не пуст — считаем успехом.
            file_ok = output_path.exists() and output_path.stat().st_size > 0
            if file_ok and "received signal 2" in stderr_tail:
                logger.info(
                    f"render_clip: ffmpeg вышел по signal 2, но файл создан — "
                    f"считаем успехом (rc={proc.returncode})"
                )
            else:
                logger.warning(f"render_clip ffmpeg error: {stderr_tail}")
                return False
        if not output_path.exists() or output_path.stat().st_size == 0:
            logger.warning("render_clip: выходной файл не создан или пуст")
            return False

        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info(
            f"Clip rendered: {output_path.name} "
            f"({start_seconds}s..{end_seconds}s, {clip_duration}s, {size_mb:.1f}MB)"
        )
        return True

    except subprocess.TimeoutExpired:
        logger.warning("render_clip: ffmpeg timeout")
        return False
    except Exception as e:
        logger.warning(f"render_clip error: {type(e).__name__}: {e}")
        return False


async def create_clip_snapshot(
    video_path: Path,
    snapshot_path: Path,
    clip_duration_seconds: float,
) -> bool:
    """
    Извлекает кадр-постер из clip (20% от длины — раньше чем у Shorts,
    т.к. clips начинаются с содержательного момента).
    Возвращает True при успехе.
    """
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg or not video_path.exists():
            return False
        seek_time = max(2.0, clip_duration_seconds * 0.20)
        cmd = [
            ffmpeg,
            "-ss", str(seek_time),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            "-y",
            str(snapshot_path),
        ]
        proc = await run_cancellable_process(cmd, timeout=60, text=True)
        if proc.returncode != 0 or not snapshot_path.exists() or snapshot_path.stat().st_size == 0:
            logger.warning(f"create_clip_snapshot: не удалось извлечь кадр из {video_path.name}")
            return False
        logger.info(f"Clip snapshot: {snapshot_path.name} (t={seek_time:.1f}s)")
        return True
    except Exception as e:
        logger.warning(f"create_clip_snapshot error: {type(e).__name__}: {e}")
        return False


def _hashtags_line(tags: list) -> str:
    """AUDIT R35 (живой прогон 2026-07-10, клип показал «##СилаБожья»):
    собрать строку хэштегов идемпотентно к тому, содержит ли тег уже «#».
    Clips хранят теги через normalize_hashtag → УЖЕ с «#», extras — без «#»;
    поэтому строим через lstrip('#') + один префикс, как это делает Shorts."""
    # AUDIT R35b: фильтруем пустые ДО среза [:4], иначе пустой/«#»-тег в первых
    # позициях уменьшал итоговое число тегов. Второй strip убирает пробел после
    # lstrip('#') у входов вида «# Сила».
    clean = [c for c in (str(t).strip().lstrip("#").strip() for t in (tags or [])) if c][:4]
    return " ".join(f"#{c}" for c in clean)


def build_clip_caption(
    candidate: dict,
    performer: str,
    real_author: str,
    real_event: str,
    format_name: str,
    yt_url: str = "",
    vk_url: str = "",
    rutube_url: str = "",
) -> str:
    """
    Строит подпись для длинного clip-фрагмента.
    Компактный стиль: заголовок - Автор ✂️ [старт — конец], ссылки с эмодзи, хэштеги.
    """
    title        = (candidate.get("title") or "").strip()
    tags         = candidate.get("hashtags") or []
    start_s      = candidate.get("start_seconds", 0)
    end_s        = candidate.get("end_seconds", 0)
    author_label = real_author or performer or ""

    def _fmt(secs: int) -> str:
        m, s = divmod(int(secs), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    kind = (candidate.get("kind") or "").strip()
    _KIND_EMOJI = {
        "sermon_highlight":   "🔥",
        "conviction_appeal":  "⚡",
        "illustration_story": "📖",
        "doctrinal_moment":   "💡",
        "application_block":  "👣",
        # QA-типы оставляем ✂️
    }
    clip_emoji = _KIND_EMOJI.get(kind, "✂️")
    range_label = f"{clip_emoji} [{_fmt(start_s)} — {_fmt(end_s)}]"

    title_tc = title_case_fragment(title) if title else ""
    if title_tc and author_label:
        if title_tc[-1] in ("?", "!"):
            first_line = f"{title_tc} {author_label} {range_label}"
        else:
            first_line = f"{title_tc} — {author_label} {range_label}"
    elif title_tc:
        first_line = f"{title_tc} {range_label}"
    else:
        first_line = range_label

    links_block = _build_links_block(yt_url, rutube_url, vk_url)
    tags_line   = _hashtags_line(tags)

    parts = [p for p in [first_line, links_block, tags_line] if p]
    return "\n\n".join(parts)



async def render_montage_short(
    source_video_path: Path,
    output_path: Path,
    fragments: list[dict],
    *,
    visual_mode: str = "full_frame_vertical",
) -> bool:
    """Склеивает несколько фрагментов в один Short 9:16 через ffmpeg concat."""
    # FIX AUDIT R4: temp_parts/concat_list_path объявляем ДО try — except ниже
    # их итерирует, и OSError из mkdir превращался в NameError из хендлера.
    temp_parts: list[Path] = []
    concat_list_path: Path | None = None
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg or not source_video_path.exists() or not fragments:
            return False
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # AUDIT R28: статичная картинка-заставка при crop в 9:16 режется криво —
        # вписываем целиком (full_frame_blur). Реальное видео остаётся на crop.
        if visual_mode == "crop_zoom" and fragments:
            _frag0_start = float(fragments[0].get("start_seconds", 0) or 0)
            if await _is_static_video(source_video_path, _frag0_start):
                visual_mode = "full_frame_vertical"
                logger.info("Montage: статичный кадр (заставка) — full_frame_blur вместо crop")

        if visual_mode == "crop_zoom":
            vf = "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=720:1280"
            _use_fc = False
        else:
            vf = (
                # AUDIT R28b: фон cover БЕЗ искажения пропорций (см. shorts_video),
                # иначе статичная заставка выходила «расплющенной».
                "[0:v]split=2[bg][fg];"
                "[bg]scale=720:1280:force_original_aspect_ratio=increase,"
                "crop=720:1280,gblur=sigma=20,setsar=1[blurred];"
                "[fg]scale=720:1280:force_original_aspect_ratio=decrease,setsar=1[small];"
                "[blurred][small]overlay=(W-w)/2:(H-h)/2[out]"
            )
            _use_fc = True

        _enc, _quality, _preset = await await_owned_coroutine(
            asyncio.to_thread(_get_video_encoder)
        )
        _hwaccel = []  # hwaccel cuda убран: CPU-фильтры несовместимы с CUDA decode
        for i, frag in enumerate(fragments):
            part_path = output_path.parent / f"{output_path.stem}_part{i}.mp4"
            temp_parts.append(part_path)
            start_s = frag["start_seconds"]
            end_s   = frag["end_seconds"]
            dur     = end_s - start_s
            if dur <= 0:
                continue
            _vf_args_m = (
                ["-filter_complex", vf, "-map", "[out]", "-map", "0:a?"]
                if _use_fc else ["-vf", vf]
            )
            cmd = [
                ffmpeg, *_hwaccel,
                "-ss", str(start_s), "-i", str(source_video_path),
                "-t", str(dur), *_vf_args_m,
                "-c:v", _enc, *_preset, *_quality,
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                "-y", str(part_path),
            ]
            # AUDIT R29: сериализуем GPU-рендер фрагмента montage.
            from core.resource_scheduler import scheduler as _sched
            async with _sched.gpu_render:
                proc = await run_cancellable_process(cmd, timeout=120, text=True)
            if proc.returncode != 0 or not part_path.exists():
                logger.warning(f"Montage: фрагмент {i} не отрендерен")
                for p in temp_parts: p.unlink(missing_ok=True)
                return False

        existing_parts = [p for p in temp_parts if p.exists() and p.stat().st_size > 0]
        if len(existing_parts) < 2:
            for p in temp_parts: p.unlink(missing_ok=True)
            return False

        concat_list_path = output_path.parent / f"{output_path.stem}_concat.txt"
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for part_path in existing_parts:
                f.write(f"file '{part_path.resolve()}'\n")

        concat_cmd = [
            ffmpeg, *_hwaccel, "-f", "concat", "-safe", "0", "-i", str(concat_list_path),
            "-c:v", _enc, *_preset, *_quality,
            "-c:a", "aac", "-b:a", "128k",
            "-vsync", "vfr",    # убирает drift между фрагментами с разным GOP
            "-af", "aresample=async=1",  # синхронизирует аудио после склейки
            "-movflags", "+faststart",
            "-y", str(output_path),
        ]
        # AUDIT R29b: финальный concat тоже кодирует NVENC — серилизуем (вне
        # уже завершённого цикла по фрагментам, так что вложенности семафора нет).
        from core.resource_scheduler import scheduler as _sched
        async with _sched.gpu_render:
            proc = await run_cancellable_process(concat_cmd, timeout=300, text=True)
        for p in temp_parts: p.unlink(missing_ok=True)
        concat_list_path.unlink(missing_ok=True)

        if proc.returncode != 0 or not output_path.exists():
            logger.warning(f"Montage: concat failed: {(proc.stderr or '')[-300:]}")
            return False

        total_dur = sum(f["end_seconds"] - f["start_seconds"] for f in fragments)
        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info(f"Montage rendered: {output_path.name} ({len(existing_parts)} фрагм., {total_dur}s, {size_mb:.1f}MB)")
        return True
    except subprocess.TimeoutExpired:
        logger.warning("render_montage_short: ffmpeg timeout")
        for p in temp_parts:
            try: p.unlink(missing_ok=True)
            except Exception: pass
        if concat_list_path:
            try: concat_list_path.unlink(missing_ok=True)
            except Exception: pass
        return False
    except Exception as e:
        logger.warning(f"render_montage_short error: {type(e).__name__}: {e}")
        for p in temp_parts:
            try: p.unlink(missing_ok=True)
            except Exception: pass
        if concat_list_path:
            try: concat_list_path.unlink(missing_ok=True)
            except Exception: pass
        return False


def _extras_text_config(max_output_tokens: int, schema: dict | None = None):
    return make_text_config_smart(
        max_output_tokens=max_output_tokens,
        thinking_level="low",
        **structured_json_config_kwargs(schema),
    )


async def _generate_extras_content(client, *, model: str, prompt: str, max_output_tokens: int, schema: dict | None):
    """Try structured JSON for extras, then fall back to legacy JSON config."""
    try:
        return await asyncio.wait_for(
            client.aio.models.generate_content(
                model=model,
                contents=[prompt],
                config=_extras_text_config(max_output_tokens, schema),
            ),
            timeout=180.0,
        )
    except asyncio.TimeoutError:
        raise
    except Exception as exc:
        # AUDIT R26: legacy-JSON retry — только при ошибке самой схемы.
        # quota/overload/timeout/auth второй запрос не лечит, лишь жжёт квоту.
        from services.gemini_error_policy import classify_gemini_error
        _decision = classify_gemini_error(exc)
        if not schema or not _decision.use_legacy_json_fallback:
            raise
        logger.warning(
            "extras_candidates: structured output схема отклонена (%s: %s) — retry legacy JSON config",
            type(exc).__name__, str(exc)[:180],
        )
        return await asyncio.wait_for(
            client.aio.models.generate_content(
                model=model,
                contents=[prompt],
                config=_extras_text_config(max_output_tokens, None),
            ),
            timeout=180.0,
        )


async def create_extras_candidates(
    ai_data: dict,
    title: str,
    performer: str,
    duration: int,
) -> dict:
    """
    Один text-only Gemini запрос на Montage + Highlights.
    Возвращает:
    {
        "montage_candidates": [...],
        "highlights_candidates": [...]
    }
    """
    if not GEMINI_CLIENTS or not HAS_GEMINI:
        return {"montage_candidates": [], "highlights_candidates": []}

    _obs_started = time.perf_counter()

    def _obs_ms() -> int:
        return int((time.perf_counter() - _obs_started) * 1000)

    try:
        format_name      = (ai_data or {}).get("format", "other") or "other"
        real_author      = (ai_data or {}).get("real_author", "") or performer or ""
        analysis_summary = (ai_data or {}).get("analysis_summary", "") or ""
        argument_arc     = (ai_data or {}).get("argument_arc", "") or ""
        key_categories   = "; ".join((ai_data or {}).get("key_categories", []) or [])
        timestamps       = (ai_data or {}).get("timestamps", "") or ""

        prompt = EXTRAS_PROMPT.format(
            title=title,
            duration=format_timestamp(duration),
            format_name=format_name,
            real_author=real_author,
            analysis_summary=analysis_summary[:800],
            argument_arc=argument_arc[:700],
            timestamps=timestamps[:1200],
            key_categories=key_categories,
        )

        _structured_schema = extras_response_schema()

        async def _call(client):
            return await _generate_extras_content(
                client, model=GEMINI_MODEL, prompt=prompt,
                max_output_tokens=14000, schema=_structured_schema,
            )

        response = await gemini_generate(GEMINI_CLIENTS, _call)

        raw_text = ""
        try:
            raw_text = response.text or ""
        except Exception:
            if response.candidates:
                for part in response.candidates[0].content.parts:
                    if not getattr(part, "thought", False):
                        raw_text += part.text or ""

        if not raw_text.strip():
            await alog_gemini_response(
                response=response, task="extras_candidates", model=GEMINI_MODEL,
                thinking_level="low", duration_ms=_obs_ms(), json_valid=False,
                error="empty_response",
            )
            return {"montage_candidates": [], "highlights_candidates": []}

        clean = re.sub(r"^```[a-z]*\s*", "", raw_text.strip())
        clean = re.sub(r"\s*```$", "", clean).strip()
        s, e = clean.find("{"), clean.rfind("}")
        if s == -1 or e <= s:
            await alog_gemini_response(
                response=response, task="extras_candidates", model=GEMINI_MODEL,
                thinking_level="low", duration_ms=_obs_ms(), json_valid=False,
                error="json_not_found",
            )
            return {"montage_candidates": [], "highlights_candidates": []}

        try:
            data = json.loads(clean[s:e + 1])
        except json.JSONDecodeError as exc:
            logger.warning("Extras candidates: JSONDecodeError: %s", exc)
            recovered = _recover_truncated_json(clean[s:])
            if recovered is None:
                await alog_gemini_response(
                    response=response, task="extras_candidates", model=GEMINI_MODEL,
                    thinking_level="low", duration_ms=_obs_ms(), json_valid=False,
                    error="json_decode_error",
                )
                return {"montage_candidates": [], "highlights_candidates": []}
            logger.info("Extras candidates: JSON восстановлен (partial/truncated)")
            data = recovered

        montage_candidates = []
        for item in (data.get("montage_candidates", []) or [])[:3]:
            if not isinstance(item, dict):
                continue
            theme = _clean_field(str(item.get("theme", "")))
            clip_title = _clean_field(str(item.get("title", "")))
            raw_tags = item.get("hashtags", [])
            hashtags = [
                str(t).strip().lstrip("#")
                for t in (raw_tags if isinstance(raw_tags, list) else [])
                if str(t).strip()
            ][:4]

            frags_raw = item.get("fragments", [])
            if not isinstance(frags_raw, list) or len(frags_raw) < 3:
                continue

            parsed_frags = []
            total_dur = 0
            prev_start = None

            for frag in frags_raw:
                if not isinstance(frag, dict):
                    continue
                ss = time_to_seconds(str(frag.get("start", "")).strip())
                ee = time_to_seconds(str(frag.get("end", "")).strip())
                if ss is None or ee is None or ee <= ss or (duration and ee > duration):
                    continue
                fd = ee - ss
                if fd < 7 or fd > 30:
                    continue
                if prev_start is not None and abs(ss - prev_start) < 90:
                    continue
                parsed_frags.append({
                    "start_seconds": ss,
                    "end_seconds": ee,
                    "summary": _clean_field(str(frag.get("summary", ""))),
                })
                prev_start = ss
                total_dur += fd

            if len(parsed_frags) >= 3 and 45 <= total_dur <= 100:
                montage_candidates.append({
                    "theme": theme,
                    "title": clip_title[:120],
                    "hashtags": hashtags,
                    "fragments": parsed_frags,
                    "total_dur": total_dur,
                })

        highlights_candidates = []
        hl = data.get("highlights", {})
        if not isinstance(hl, dict):
            # Поддерживаем альтернативную схему, которую часто возвращают structured prompts.
            hl_list = data.get("highlights_candidates", [])
            hl = hl_list[0] if isinstance(hl_list, list) and hl_list and isinstance(hl_list[0], dict) else {}
        if isinstance(hl, dict):
            clip_title = _clean_field(str(hl.get("title", "")))
            raw_tags = hl.get("hashtags", [])
            hashtags = [
                str(t).strip().lstrip("#")
                for t in (raw_tags if isinstance(raw_tags, list) else [])
                if str(t).strip()
            ][:4]

            frags_raw = hl.get("fragments", [])
            parsed_frags = []
            total_dur = 0

            if isinstance(frags_raw, list):
                for frag in frags_raw:
                    if not isinstance(frag, dict):
                        continue
                    ss = time_to_seconds(str(frag.get("start", "")).strip())
                    ee = time_to_seconds(str(frag.get("end", "")).strip())
                    if ss is None or ee is None or ee <= ss or (duration and ee > duration):
                        continue
                    fd = ee - ss
                    if fd < 4 or fd > 18:
                        continue
                    parsed_frags.append({
                        "start_seconds": ss,
                        "end_seconds": ee,
                        "hook": _clean_field(str(frag.get("hook", ""))),
                    })
                    total_dur += fd

            if len(parsed_frags) >= 4 and 50 <= total_dur <= 100:
                highlights_candidates.append({
                    "title": clip_title[:120],
                    "hashtags": hashtags,
                    "fragments": parsed_frags,
                    "total_dur": total_dur,
                })

        logger.info(
            f"Extras candidates: montage={len(montage_candidates)} "
            f"highlights={len(highlights_candidates)}"
        )
        await alog_gemini_response(
            response=response, task="extras_candidates", model=GEMINI_MODEL,
            thinking_level="low", duration_ms=_obs_ms(), json_valid=True,
        )
        return {
            "montage_candidates": montage_candidates,
            "highlights_candidates": highlights_candidates,
        }

    except Exception as e:
        logger.warning(f"create_extras_candidates error: {type(e).__name__}: {e}")
        await alog_gemini_run(
            task="extras_candidates", model=GEMINI_MODEL, thinking_level="low",
            duration_ms=_obs_ms(), json_valid=False,
            error=f"{type(e).__name__}: {str(e)[:300]}",
        )
        return {"montage_candidates": [], "highlights_candidates": []}


def build_montage_caption(
    theme: str, title: str, performer: str, real_author: str,
    format_name: str, fragment_count: int, hashtags: list[str],
    yt_url: str = "", vk_url: str = "", rutube_url: str = "",
) -> str:
    author_label = real_author or performer or ""
    title_tc     = title_case_fragment(title) if title else ""
    first_line   = f"{title_tc} - {author_label}" if author_label else title_tc
    context      = f"Тематическая подборка: {theme}" if theme else ""
    links_block  = _build_links_block(yt_url, rutube_url, vk_url)
    tags_line    = _hashtags_line(hashtags)
    parts = [p for p in [first_line, context, links_block, tags_line] if p]
    return "\n\n".join(parts)


def build_highlights_caption(
    title: str, performer: str, real_author: str, format_name: str,
    fragment_count: int, hashtags: list[str],
    yt_url: str = "", vk_url: str = "", rutube_url: str = "",
) -> str:
    author_label = real_author or performer or ""
    title_tc     = title_case_fragment(title) if title else ""
    first_line   = f"{title_tc} - {author_label}" if author_label else title_tc
    context      = f"Лучшие моменты ({fragment_count} фрагментов)"
    links_block  = _build_links_block(yt_url, rutube_url, vk_url)
    tags_line    = _hashtags_line(hashtags)
    parts = [p for p in [first_line, context, links_block, tags_line] if p]
    return "\n\n".join(parts)


