#!/usr/bin/env python3
"""
Shorts / Video processing — субтитры, рендер, постеры.
Извлечено из bot.py строки 9164–10707.
"""
from __future__ import annotations

from core.globals import (
    DOWNLOAD_DIR, THUMBS_DIR, HAS_PILLOW,
    html_mod,                     # FIX shorts_video
)
from services.ffmpeg import _get_video_encoder, YTDLP_BASE_ARGS, _find_silence_end, _detect_black_bars, _is_static_video  # FIX shorts_video
from core.database import settings_get   # FIX shorts_video
from core.text_utils import normalize_common_typos, title_case_fragment  # FIX shorts_video
from core.url_utils import get_youtube_video_url # FIX shorts_video

import asyncio
import logging
import os         # FIX shorts_video
import re
import shutil
import subprocess
import threading  # FIX shorts_video
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

def _pick_video_file(media_id: str) -> Optional[Path]:
    """Возвращает первый найденный видеофайл с предпочтительным расширением."""
    preferred_exts = [".mp4", ".mkv", ".webm", ".mov"]
    files = [p for p in DOWNLOAD_DIR.glob(f"{media_id}_video.*") if p.is_file()]
    for ext in preferred_exts:
        for p in files:
            if p.suffix.lower() == ext:
                return p
    return None


async def download_video_for_shorts(url: str, media_id: str, workdir: Optional[Path] = None) -> Optional[Path]:
    """
    Скачивает видео в mp4 для вырезки Shorts.
    Использует bestvideo[height<=720]+bestaudio — оптимально для Shorts.
    Возвращает Path к mp4 или None при ошибке.
    """
    # 2026-06-11: Пытаемся реюзить видео из workdir (temp), если оно там есть.
    # Это экономит трафик и время, если пайплайн LiveDub уже скачал оригинал.
    # FIX AUDIT R4: пропускаем недокачанные (.part/.ytdl) и audio-only файлы
    # (original_video.f140.m4a) — как download_original_video в eng_subtitles,
    # иначе рендер шорта падал на crop-фильтре или выходил обрезанным.
    if workdir and workdir.exists():
        from services.eng_subtitles import _has_video_stream
        for existing in workdir.glob("original_video.*"):
            if existing.suffix.lower() in {".part", ".ytdl"}:
                continue
            if existing.is_file() and _has_video_stream(existing):
                logger.info(f"Shorts reuse video from workdir: {existing.name}")
                return existing

    existing = _pick_video_file(media_id)
    if existing:
        return existing
    try:
        cmd = YTDLP_BASE_ARGS + [
            "--format", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]/best",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--output", str(DOWNLOAD_DIR / f"{media_id}_video.%(ext)s"),
            url,
        ]
        loop = asyncio.get_running_loop()
        proc = await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=900),
        )
        if proc.returncode != 0:
            logger.warning(f"Shorts video download failed: {proc.stderr[-300:]}")
            return None
        return _pick_video_file(media_id)
    except Exception as e:
        logger.warning(f"Shorts download_video error: {e}")
        return None


def get_shorts_visual_mode(format_name: str, visual_mode: str = "auto") -> str:
    """
    Возвращает итоговый visual_mode для рендера short.

    Режимы:
    - crop_zoom          — medium crop: берём центральные ~56% ширины (crop по 9:16),
                           масштабируем до 720×1280. Спикер заметно ближе, жесты и
                           плечи остаются в кадре.
    - full_frame_vertical — исходный 16:9 кадр целиком на вертикальном 9:16 холсте.
                           Фон — размытая копия того же кадра (blur background).
                           ВНИМАНИЕ: для 16:9 источника контент занимает лишь ~30%
                           высоты кадра (405 из 1280 px) — остальное blur-полосы.
                           Используй только через явный visual_mode='full_frame_vertical'.
    - auto               — выбирает режим по format_name.

    FIXED #138: full_frame_blur убран из auto-пути — для 16:9 контента контент
    занимал лишь ~30% кадра. Теперь crop_zoom для всех форматов по умолчанию.
    """
    if visual_mode != "auto":
        return visual_mode
    # FIXED #138: crop_zoom для всех форматов — full_frame_vertical доступен
    # только через явный visual_mode='full_frame_vertical'
    return "crop_zoom"


# ─── GPU/CPU encoder detection ───────────────────────────────

_VIDEO_ENCODER: str | None = None  # кэш результата проверки

async def render_short_clip(
    source_video_path: Path,
    output_path: Path,
    start_seconds: int,
    end_seconds: int,
    *,
    visual_mode: str = "full_frame_vertical",
) -> bool:
    """
    Вырезает short-клип из исходного видео через ffmpeg.
    Итоговый формат: 9:16 (720×1280).

    visual_mode:
    - crop_zoom           — medium crop: центральные ~56% ширины исходника под 9:16.
                            Спикер заметно ближе, голова/плечи/жесты в кадре.
                            Для sermon, lecture.
    - full_frame_vertical — исходный кадр целиком, фон — блюр той же картинки.
                            Для Q&A, панелей, интервью — никого не теряем.

    Возвращает True при успехе, False при ошибке.
    """
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("render_short_clip: ffmpeg не найден")
            return False

        if not source_video_path.exists():
            logger.warning(f"render_short_clip: исходный файл не найден: {source_video_path}")
            return False

        if end_seconds <= start_seconds:
            logger.warning(f"render_short_clip: невалидный диапазон {start_seconds}..{end_seconds}")
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Корректируем точку конца до ближайшей паузы (±2–3 сек от Gemini-оценки)
        adjusted_end = await _find_silence_end(source_video_path, float(end_seconds))
        # Guard: silence snap must not shrink short below 10s or extend beyond +10s
        min_end = start_seconds + max(10, int((end_seconds - start_seconds) * 0.5))
        max_end = end_seconds + 10
        if min_end < adjusted_end <= max_end and abs(adjusted_end - end_seconds) > 0.1:
            logger.info(f"Short end adjusted: {end_seconds}s → {adjusted_end:.1f}s (silence snap)")
            end_seconds = int(round(adjusted_end))
        clip_duration = end_seconds - start_seconds
        if clip_duration <= 0:
            logger.warning("render_short_clip: clip_duration ≤ 0 после коррекции паузы")
            return False

        # Детектируем чёрные полосы (letterbox/pillarbox) и срезаем их
        black_bars = await _detect_black_bars(source_video_path, float(start_seconds))

        # AUDIT R28: статичная картинка-заставка (аудио-проповедь с обложкой)
        # при crop в 9:16 режется криво (заголовок за краем). Для таких кадров
        # переключаемся на full_frame_blur — картинка целиком по центру, сверху/
        # снизу размытый фон, субтитры читаются на нём. Реальное видео
        # проповедника остаётся на crop_zoom (там кадр заполняется отлично).
        if visual_mode == "crop_zoom" and await _is_static_video(source_video_path, float(start_seconds)):
            visual_mode = "full_frame_vertical"
            # AUDIT R47 (живой скриншот: «видно только левый бок видео, кривой
            # blur»): cropdetect ищет letterbox-полосы в РЕАЛЬНОМ видеокадре, но
            # статичная промо-заставка — это дизайн-графика с крупными тёмными/
            # цветными блоками (фон, текст), а не чёрные полосы. cropdetect(limit=32)
            # ловит «серые/тёмные полосы» по своему же docstring — на несимметрично
            # свёрстанной заставке он срезает часть картинки асимметрично, и
            # blur-фон строится уже из обрезанного (визуально «съехавшего») кадра.
            # full_frame_vertical и так показывает картинку ЦЕЛИКОМ — обрезка по
            # чёрным полосам здесь не нужна и только портит статичные заставки.
            black_bars = ""
            logger.info("Short: статичный кадр (заставка) — режим full_frame_blur вместо crop, cropdetect игнорируется")

        _use_filter_complex = False   # True для blur-overlay графа с лейблами
        if visual_mode == "crop_zoom":
            # Medium crop для одного спикера (sermon, lecture):
            # берём центральные ~56% ширины (соотношение 9:16) и масштабируем до 720×1280.
            bc = f"{black_bars}," if black_bars else ""
            vf = (
                f"{bc}crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
                "scale=720:1280"
            )
            mode_label = "crop_zoom(medium)"
        else:
            if black_bars:
                vf = (
                    f"[0:v]{black_bars}[clean];"
                    "[clean]split=2[bg][fg];"
                    # AUDIT R28b (оператор: «засплющило, расплющенная ава»):
                    # фон масштабируем С СОХРАНЕНИЕМ пропорций (cover=increase+
                    # crop), иначе scale=720:1280 растягивал 16:9→9:16 и для
                    # статичной картинки этот сплющенный блюр-фон и был всей
                    # «расплющенной» картинкой. setsar=1 — квадратный пиксель,
                    # чтобы ничто не растягивалось при показе.
                    "[bg]scale=720:1280:force_original_aspect_ratio=increase,"
                    "crop=720:1280,gblur=sigma=20,setsar=1[blurred];"
                    "[fg]scale=720:1280:force_original_aspect_ratio=decrease,setsar=1[small];"
                    "[blurred][small]overlay=(W-w)/2:(H-h)/2[out]"
                )
                _use_filter_complex = True
            else:
                vf = (
                    "[0:v]split=2[bg][fg];"
                    # AUDIT R28b (оператор: «засплющило, расплющенная ава»):
                    # фон масштабируем С СОХРАНЕНИЕМ пропорций (cover=increase+
                    # crop), иначе scale=720:1280 растягивал 16:9→9:16 и для
                    # статичной картинки этот сплющенный блюр-фон и был всей
                    # «расплющенной» картинкой. setsar=1 — квадратный пиксель,
                    # чтобы ничто не растягивалось при показе.
                    "[bg]scale=720:1280:force_original_aspect_ratio=increase,"
                    "crop=720:1280,gblur=sigma=20,setsar=1[blurred];"
                    "[fg]scale=720:1280:force_original_aspect_ratio=decrease,setsar=1[small];"
                    "[blurred][small]overlay=(W-w)/2:(H-h)/2[out]"
                )
                _use_filter_complex = True
            mode_label = "full_frame_blur"
        _enc, _quality, _preset = _get_video_encoder()
        _hwaccel = []  # hwaccel cuda убран: CPU-фильтры несовместимы с CUDA decode
        _vf_args = (
            ["-filter_complex", vf, "-map", "[out]", "-map", "0:a?"]
            if _use_filter_complex else
            ["-vf", vf]
        )
        cmd = [
            ffmpeg,
            *_hwaccel,
            "-ss", str(start_seconds),
            "-i", str(source_video_path),
            "-t", str(clip_duration),
            *_vf_args,
            "-c:v", _enc,
            *_preset,
            *_quality,
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-y",
            str(output_path),
        ]

        # AUDIT R29: сериализуем GPU-рендер — параллельные видео не дерутся за
        # одну видеокарту (иначе h264_nvenc упирался в 15-мин таймаут).
        from core.resource_scheduler import scheduler as _sched
        async with _sched.gpu_render:
            proc = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=600),
            )

        if proc.returncode != 0:
            stderr = (proc.stderr or "")[-1000:]
            logger.warning(f"render_short_clip ffmpeg error ({mode_label}): {stderr}")
            return False

        if not output_path.exists() or output_path.stat().st_size == 0:
            logger.warning("render_short_clip: выходной файл не создан или пуст")
            return False

        # Меньше 10KB для видео — явно битый файл (ffmpeg завершился без ошибки, но ничего не записал)
        if output_path.stat().st_size < 10_240:
            stderr = (proc.stderr or "")[-1000:]
            logger.warning(
                f"render_short_clip: файл подозрительно мал ({output_path.stat().st_size} байт). "
                f"ffmpeg stderr: {stderr}"
            )
            output_path.unlink(missing_ok=True)
            return False

        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info(
            f"Short rendered [{mode_label}] 9:16: {output_path.name} "
            f"({start_seconds}s..{end_seconds}s, {clip_duration}s, {size_mb:.1f}MB)"
        )
        return True

    except subprocess.TimeoutExpired:
        logger.warning("render_short_clip: ffmpeg timeout")
        return False
    except Exception as e:
        logger.warning(f"render_short_clip error: {type(e).__name__}: {e}")
        return False


async def postprocess_short(
    input_path: Path,
    output_path: Path,
    *,
    normalize_audio: bool = True,
    speed: float = 1.0,
) -> bool:
    """
    Постобработка short-клипа: нормализация громкости и/или ускорение.
    Применяется только к shorts, MP3-пайплайн не затрагивается.

    - normalize_audio: loudnorm через ffmpeg (EBU R128, target -16 LUFS).
    - speed: ускорение без изменения тона (atempo + setpts).
      Допустимые значения: 1.0, 1.1, 1.3, 1.5.
      При 1.0 этот фильтр не применяется.

    Возвращает True при успехе, False при ошибке (input_path при этом остаётся нетронутым).
    """
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("postprocess_short: ffmpeg не найден")
            return False

        if not input_path.exists():
            logger.warning(f"postprocess_short: входной файл не найден: {input_path}")
            return False

        # Нормализован ли speed
        speed = float(speed)
        use_speed = abs(speed - 1.0) > 0.01

        # Строим audio и video filter
        # ── audio ──────────────────────────────────────────────
        audio_filters = []
        if normalize_audio:
            # loudnorm: target integrated loudness -16 LUFS, true peak -1.5 dBTP
            audio_filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        if use_speed:
            # atempo поддерживает диапазон 0.5–2.0
            audio_filters.append(f"atempo={speed}")

        # ── video ──────────────────────────────────────────────
        video_filters = []
        if use_speed:
            # setpts уменьшает PTS пропорционально скорости
            video_filters.append(f"setpts={1.0/speed}*PTS")

        vf_arg = ",".join(video_filters) if video_filters else None
        af_arg = ",".join(audio_filters) if audio_filters else None

        if not vf_arg and not af_arg:
            # Нечего делать — просто скопируем поток

            shutil.copy2(input_path, output_path)
            return True

        _enc, _quality, _preset = _get_video_encoder()
        _hwaccel = []  # hwaccel cuda убран: CPU-фильтры несовместимы с CUDA decode
        cmd = [ffmpeg, *_hwaccel, "-i", str(input_path)]
        if vf_arg:
            cmd += ["-vf", vf_arg]
        if af_arg:
            cmd += ["-af", af_arg]
        cmd += [
            "-c:v", _enc,
            *_preset,
            *_quality,
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-y",
            str(output_path),
        ]

        # AUDIT R29b: постобработка тоже кодирует NVENC — серилизуем через тот же
        # GPU-семафор, иначе при 3 видео параллельно шли 3 NVENC-сессии разом
        # (render одного + postprocess другого + burn третьего) — ровно та
        # гонка за видеокарту, ради которой вводился R29.
        from core.resource_scheduler import scheduler as _sched
        async with _sched.gpu_render:
            proc = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=600),
            )
        if proc.returncode != 0:
            logger.warning(f"postprocess_short ffmpeg error: {(proc.stderr or '')[-800:]}")
            return False
        if not output_path.exists() or output_path.stat().st_size == 0:
            logger.warning("postprocess_short: выходной файл не создан или пуст")
            return False

        size_mb = output_path.stat().st_size / (1024 * 1024)
        logger.info(
            f"postprocess_short: normalize={normalize_audio} speed={speed} → "
            f"{output_path.name} ({size_mb:.1f}MB)"
        )
        return True

    except subprocess.TimeoutExpired:
        logger.warning("postprocess_short: ffmpeg timeout")
        return False
    except Exception as e:
        logger.warning(f"postprocess_short error: {type(e).__name__}: {e}")
        return False


# ── Субтитры для Shorts ───────────────────────────────────────

try:
    from faster_whisper import WhisperModel
    HAS_FASTER_WHISPER = True
except ImportError:
    HAS_FASTER_WHISPER = False


def _wrap_subtitle_text(text: str, max_chars: int = 38) -> str:
    """Разбивает текст субтитра на 1–2 строки, не разрывая слова."""
    words = text.split()
    if not words:
        return text
    lines: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        add_len = len(word) + (1 if current else 0)
        if current_len + add_len <= max_chars:
            current.append(word)
            current_len += add_len
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
            current_len = len(word)
    if current:
        lines.append(" ".join(current))
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
    return "\n".join(lines)


def _seconds_to_ass_time(seconds: float) -> str:
    """Секунды → ASS-время H:MM:SS.cc

    FIX: отрицательный вход (Whisper иногда отдаёт start<0) раньше давал
    битый таймкод вида '-1:59:59.-50', из-за которого libass/ffmpeg ломали
    рендеринг всей строки субтитров. Клампим к 0.
    """
    if seconds < 0:
        seconds = 0.0
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:  # защита от округления 0.999 → 100
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _pick_subtitle_font() -> str:
    """Выбирает лучший доступный шрифт для субтитров (кириллица, 2026).

    Приоритет: ExtraBold/Black-начертания — они не теряются на ярком видео.
    """
    candidates = [
        # Montserrat ExtraBold — топ-1 для Shorts/Reels 2026
        ("Montserrat ExtraBold", [
            "/usr/share/fonts/truetype/montserrat/Montserrat-ExtraBold.ttf",
            "/usr/local/share/fonts/Montserrat-ExtraBold.ttf",
            "C:/Windows/Fonts/Montserrat-ExtraBold.ttf",
        ]),
        # Montserrat Black — максимальный вес
        ("Montserrat Black", [
            "/usr/share/fonts/truetype/montserrat/Montserrat-Black.ttf",
            "/usr/local/share/fonts/Montserrat-Black.ttf",
            "C:/Windows/Fonts/Montserrat-Black.ttf",
        ]),
        # Montserrat Bold — fallback если ExtraBold/Black не установлены
        ("Montserrat Bold", [
            "/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf",
            "/usr/local/share/fonts/Montserrat-Bold.ttf",
            "C:/Windows/Fonts/Montserrat-Bold.ttf",
        ]),
        # Montserrat SemiBold — последний вариант Montserrat
        ("Montserrat SemiBold", [
            "/usr/share/fonts/truetype/montserrat/Montserrat-SemiBold.ttf",
            "/usr/local/share/fonts/Montserrat-SemiBold.ttf",
            "C:/Windows/Fonts/Montserrat-SemiBold.ttf",
        ]),
        # Nunito ExtraBold — отличная кириллица, компактная
        ("Nunito ExtraBold", [
            "/usr/share/fonts/truetype/nunito/Nunito-ExtraBold.ttf",
            "/usr/local/share/fonts/Nunito-ExtraBold.ttf",
            "C:/Windows/Fonts/Nunito-ExtraBold.ttf",
        ]),
        # PT Sans Bold — российский стандарт, превосходная кириллица
        ("PT Sans Bold", [
            "/usr/share/fonts/truetype/pt-sans/PTSans-Bold.ttf",
            "/usr/share/fonts/truetype/ptfonts/PTSans-Bold.ttf",
            "/usr/local/share/fonts/PTSans-Bold.ttf",
            "C:/Windows/Fonts/PTSans-Bold.ttf",
        ]),
        # Inter Bold — современный, читаемый
        ("Inter Bold", [
            "/usr/share/fonts/truetype/inter/Inter-Bold.ttf",
            "/usr/local/share/fonts/Inter-Bold.ttf",
            "C:/Windows/Fonts/Inter-Bold.ttf",
        ]),
        # Inter SemiBold — fallback Inter
        ("Inter SemiBold", [
            "/usr/share/fonts/truetype/inter/Inter-SemiBold.ttf",
            "/usr/local/share/fonts/Inter-SemiBold.ttf",
            "C:/Windows/Fonts/Inter-SemiBold.ttf",
        ]),
        # Noto Sans Bold — широкая поддержка кириллицы
        ("Noto Sans Bold", [
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/noto/NotoSans-Bold.ttf",
            "C:/Windows/Fonts/NotoSans-Bold.ttf",
        ]),
        # Roboto Bold — широко доступен
        ("Roboto Bold", [
            "/usr/share/fonts/truetype/roboto/Roboto-Bold.ttf",
            "C:/Windows/Fonts/Roboto-Bold.ttf",
        ]),
        # Arial Bold — системный fallback
        ("Arial Bold", [
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ]),
    ]
    for font_name, paths in candidates:
        for p in paths:
            if os.path.exists(p):
                return font_name
    return "Arial"  # системный fallback


def _normalize_word_timings(words: list[dict]) -> list[dict]:
    """
    Нормализует тайминги слов от Whisper:
    - минимальная длительность слова 80ms
    - убирает overlaps между соседними словами
    - сглаживает слишком короткие интервалы
    """
    if not words:
        return words
    result = []
    MIN_DUR = 0.08  # 80ms минимум на word
    for i, w in enumerate(words):
        start = float(w.get("start", 0))
        end   = float(w.get("end", start + MIN_DUR))
        # Минимальная длительность
        if end - start < MIN_DUR:
            end = start + MIN_DUR
        # Убираем overlap с предыдущим словом
        if result and start < result[-1]["end"]:
            start = result[-1]["end"]
            if end <= start:
                end = start + MIN_DUR
        result.append({**w, "start": start, "end": end})
    return result


def _merge_hyphenated_particles(words: list[dict]) -> list[dict]:
    """
    Склеивает русские частицы через дефис обратно в одно слово.

    Whisper разбивает «что-то» на два токена: «что» + «-то».
    В субтитрах это выглядит как «что -то» с лишним пробелом.

    Частицы: -то, -либо, -нибудь, -ка, -таки, -де, -с, -ж, -же
    Также: кое- (префикс)
    """
    if len(words) < 2:
        return words

    PARTICLES = {"-то", "-либо", "-нибудь", "-ка", "-таки", "-де", "-с", "-ж", "-же"}

    result = []
    i = 0
    while i < len(words):
        w = words[i]
        word_text = w.get("word", "").strip()

        # Текущее слово — частица с дефисом → склеиваем с предыдущим
        if word_text.lower() in PARTICLES and result:
            prev = result[-1]
            merged_word = prev.get("word", "").strip() + word_text
            result[-1] = {
                **prev,
                "word": merged_word,
                "end": w.get("end", prev.get("end", 0)),
            }
        # Текущее слово заканчивается дефисом (кое-) → склеиваем со следующим
        elif word_text.endswith("-") and i + 1 < len(words):
            next_w = words[i + 1]
            merged_word = word_text + next_w.get("word", "").strip()
            result.append({
                **w,
                "word": merged_word,
                "end": next_w.get("end", w.get("end", 0)),
            })
            i += 2
            continue
        else:
            result.append({**w})

        i += 1

    return result


_PURE_PUNCT_RE = re.compile(r"^[^\w]+$", re.UNICODE)


def _merge_orphan_punctuation(words: list[dict]) -> list[dict]:
    """Приклеивает токен из ОДНОЙ пунктуации к предыдущему слову.

    Whisper иногда отдаёт закрывающую кавычку+знак («?»» после «Спаситель»)
    отдельным word-токеном без единой буквы. Если оставить его отдельным
    элементом, _chunk_words_smart/_wrap_chunk_to_lines может выбрать точку
    разрыва строки ровно перед ним — субтитр переносит "?»" на новую строку
    в одиночестве (живой пример: "«Тебе нужен Спаситель" / "?» Тебе нужен").
    Склеиваем без пробела к предыдущему токену, тайминг предыдущего слова
    продлеваем до конца пунктуации.
    """
    if len(words) < 2:
        return words
    result: list[dict] = []
    for w in words:
        word_text = w.get("word", "").strip()
        if word_text and result and _PURE_PUNCT_RE.match(word_text):
            prev = result[-1]
            result[-1] = {
                **prev,
                "word": prev.get("word", "").strip() + word_text,
                "end": w.get("end", prev.get("end", 0)),
            }
        else:
            result.append({**w})
    return result


def _chunk_words_smart(words: list[dict], max_chars: int = 36, max_pause: float = 0.4) -> list[list[dict]]:
    """
    Умная группировка слов в subtitle chunks.

    Логика:
    - режет по паузам между словами (> max_pause секунд)
    - режет по пунктуации (., !, ?, ,)
    - соблюдает лимит символов
    - старается не оставлять короткие служебные слова в одиночестве
    - максимум 2 строки на chunk
    """
    if not words:
        return []

    # Служебные слова — не разрывать после них
    PREPOSITIONS = {"в", "на", "за", "из", "по", "к", "с", "о", "у", "до", "об",
                    "от", "под", "над", "при", "про", "без", "для", "и", "а", "но",
                    "или", "не", "ни", "же", "что", "как", "это", "то"}

    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_len = 0

    for i, w in enumerate(words):
        word_text = w.get("word", "").strip()
        word_len  = len(word_text) + (1 if current else 0)

        # Определяем паузу после предыдущего слова
        pause = 0.0
        if current and i > 0:
            pause = w["start"] - words[i - 1]["end"]

        # Пунктуация в конце предыдущего слова
        prev_text = current[-1].get("word", "").strip() if current else ""
        ends_sentence = prev_text.endswith((".", "!", "?"))
        ends_clause   = prev_text.endswith(",")

        # Решаем — резать или нет
        should_cut = False
        if current:
            if current_len + word_len > max_chars:
                should_cut = True
            elif pause > max_pause:
                should_cut = True
            elif ends_sentence:
                should_cut = True
            elif ends_clause and current_len > max_chars * 0.5:
                should_cut = True

        # Не режем если предыдущее слово — предлог/союз
        if should_cut and prev_text.lower() in PREPOSITIONS:
            should_cut = False

        if should_cut and current:
            chunks.append(current)
            current = []
            current_len = 0

        current.append(w)
        current_len += word_len

    if current:
        chunks.append(current)

    return chunks


def _wrap_chunk_to_lines(words: list[dict], max_chars: int = 36) -> str:
    """
    Разбивает chunk слов на 1-2 строки, балансируя длину.
    Возвращает текст с \\N между строками если нужно 2 строки.
    """
    text = " ".join(w.get("word", "").strip() for w in words)
    if len(text) <= max_chars:
        return text

    # Пытаемся найти красивую точку разрыва ближе к середине
    words_list = text.split()
    mid = len(text) // 2
    best_pos = -1
    best_dist = len(text)

    pos = 0
    for i, word in enumerate(words_list[:-1]):
        pos += len(word) + 1
        dist = abs(pos - mid)
        if dist < best_dist:
            best_dist = dist
            best_pos = i + 1

    if best_pos > 0:
        line1 = " ".join(words_list[:best_pos])
        line2 = " ".join(words_list[best_pos:])
        return f"{line1}\\N{line2}"

    return text


def _fill_timing_gaps(words: list[dict], max_gap: float = 0.8) -> list[dict]:
    """
    Заполняет дыры в тайминге слов.

    Когда Whisper пропускает английское слово (language="ru"), в тайминге
    образуется дыра — караоке молчит и потом резко прыгает.
    Решение: если между словами дыра > max_gap секунд,
    растягиваем end предыдущего слова до start следующего.
    """
    if len(words) < 2:
        return words
    result = []
    for i, w in enumerate(words):
        if i == 0:
            result.append({**w})
            continue
        gap = w["start"] - result[-1]["end"]
        if gap > max_gap:
            result[-1] = {**result[-1], "end": w["start"]}
        result.append({**w})
    return result


def _generate_ass_from_segments(segments: list[dict], karaoke: bool = True) -> str:
    """
    Генерирует .ass-файл из сегментов (faster-whisper). Стиль 2026.

    karaoke=True  — покадровая подсветка слов:
      · текущее слово  → яркий жёлтый &H0000E5FF
      · остальные      → чистый белый &H00FFFFFF  (никакого серого!)
      · реализация     → per-word Dialogue-строки: каждое слово получает
        собственную строку на время [word.start, word.end], где показывается
        весь chunk с нужным словом в жёлтом, остальные белые.
        Это единственный способ гарантировать "только одно слово жёлтое"
        в libass/ffmpeg без побочных эффектов.

    karaoke=False — чистые белые субтитры с fade-анимацией.

    Стиль: Montserrat ExtraBold / PT Sans Bold, 62px, spacing 1.5,
    outline 3.5, shadow 1.5, MarginV 165 — выше нижнего UI Shorts.
    """
    # ── Цвета (ASS формат &HAABBGGRR) ────────────────────────────
    # &H0000E5FF = BGR(FF,E5,00) = RGB(255,229,0) — насыщенный жёлтый
    # &H00FFFFFF = белый (чистый, никакой прозрачности/серого)
    COLOUR_ACTIVE   = "&H0000E5FF"
    COLOUR_INACTIVE = "&H00FFFFFF"

    # Fade — плавное появление/исчезновение только для plain режима
    FADE_PLAIN = r"{\fad(80,60)}"

    font_name = _pick_subtitle_font()

    # ── ASS-заголовок — премиальный стиль 2026 ────────────────────
    # Fontsize    62    — крупнее, чётче на мобильном
    # Spacing     1.5   — межбуквенный интервал, ощущение качества
    # ScaleX      97    — лёгкое горизонтальное сжатие, элегантность
    # Outline     3.5   — толстый контур, читается на любом фоне
    # Shadow      1.5   — мягкая тень, глубина
    # MarginV     165   — выше на 35px: нижний UI Shorts не перекрывает
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 720\nPlayResY: 1280\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},62,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,"
        "1,0,0,0,97,100,1.5,0,1,3.5,1.5,2,30,30,165,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    # ── Вспомогательная функция сборки слов из сегментов ─────────
    def _collect_words(segs: list[dict]) -> list[dict]:
        result: list[dict] = []
        for seg in segs:
            words_raw = seg.get("words") or []
            if words_raw:
                for w in words_raw:
                    word = str(w.get("word", "")).strip()
                    if word:
                        result.append({
                            "word":  word,
                            "start": float(w.get("start", seg.get("start", 0))),
                            "end":   float(w.get("end",   seg.get("end",   0))),
                        })
            else:
                text = seg.get("text", "").strip()
                if not text:
                    continue
                seg_start = float(seg.get("start", 0))
                seg_end   = float(seg.get("end", seg_start + 1))
                words_split = text.split()
                # 2026-06-11: Повышаем точность псевдо-слов при отсутствии word_timestamps
                dur_per = (seg_end - seg_start) / max(len(words_split), 1)
                for i, word in enumerate(words_split):
                    result.append({
                        "word":  word,
                        "start": seg_start + i * dur_per,
                        "end":   seg_start + (i + 0.9) * dur_per,
                    })
        return result

    # ── Plain subtitle режим (karaoke=False) ─────────────────────
    if not karaoke:
        all_words = _collect_words(segments)
        if not all_words:
            return header
        all_words = _normalize_word_timings(all_words)
        all_words = _fill_timing_gaps(all_words)
        all_words = _merge_hyphenated_particles(all_words)
        all_words = _merge_orphan_punctuation(all_words)

        chunks    = _chunk_words_smart(all_words, max_chars=38)
        ass_lines = [header]
        for chunk in chunks:
            if not chunk:
                continue
            t_s  = _seconds_to_ass_time(chunk[0]["start"])
            t_e  = _seconds_to_ass_time(chunk[-1]["end"])
            text = _wrap_chunk_to_lines(chunk, max_chars=38)
            text = text.replace("{", "\\{").replace("}", "\\}")
            ass_lines.append(f"Dialogue: 0,{t_s},{t_e},Default,,0,0,0,,{FADE_PLAIN}{text}")
        return "\n".join(ass_lines)

    # ── Karaoke режим (karaoke=True) — per-word Dialogue ─────────
    #
    # ИСПРАВЛЕН баг старой версии:
    #   Старый код: {\cWHITE}{\k50}{\cYELLOW}слово  — \cYELLOW применялся
    #   немедленно, ВСЕ слова были жёлтыми одновременно. Каракое не работало.
    #
    # Новый подход: для каждого слова — отдельная Dialogue-строка
    #   на интервал [word.start, word.end].
    #   В строке показывается ВЕСЬ chunk:
    #     · текущее слово  → {\c&H0000E5FF} (жёлтый)  + {\r} для сброса
    #     · остальные      → {\c&H00FFFFFF} (белый)
    #   Таким образом в каждый момент времени РОВНО ОДНО слово жёлтое,
    #   все остальные — чистый белый. Никакого серого.
    #
    all_words = _collect_words(segments)
    if not all_words:
        return header

    all_words = _normalize_word_timings(all_words)
    all_words = _fill_timing_gaps(all_words)
    all_words = _merge_hyphenated_particles(all_words)
    all_words = _merge_orphan_punctuation(all_words)

    chunks    = _chunk_words_smart(all_words, max_chars=38, max_pause=0.35)
    ass_lines = [header]

    for chunk in chunks:
        if not chunk:
            continue

        # Предподготовка: очищаем спецсимволы ASS в тексте слов
        clean = [
            w["word"].replace("{", "\\{").replace("}", "\\}")
            for w in chunk
        ]

        # Для каждого слова — своя Dialogue-строка.
        #
        # ИСПРАВЛЕН БАГ: каждая строка живёт от w["start"] до НАЧАЛА
        # следующего слова (не до w["end"]). Это убирает мигание/пропадание
        # субтитров в паузах между словами — экран не гаснет.
        # Для последнего слова в chunk используем chunk[-1]["end"].
        #
        # ИСПРАВЛЕН БАГ: вместо {\r} (сброс ВСЕХ override-тегов) используем
        # явный {\c&HWHITE} — безопаснее, не трогает другие теги.
        chunk_end_time = chunk[-1]["end"]

        for i, w in enumerate(chunk):
            w_s = _seconds_to_ass_time(w["start"])
            # Строка видна до старта следующего слова (или до конца chunk)
            if i + 1 < len(chunk):
                w_e = _seconds_to_ass_time(chunk[i + 1]["start"])
            else:
                w_e = _seconds_to_ass_time(chunk_end_time)

            # Защита от нулевого или отрицательного интервала
            if w["start"] >= (chunk[i + 1]["start"] if i + 1 < len(chunk) else chunk_end_time):
                w_e = _seconds_to_ass_time(w["start"] + 0.08)

            parts: list[str] = []
            for j, word_text in enumerate(clean):
                if j == i:
                    parts.append(f"{{\\c{COLOUR_ACTIVE}}}{word_text}{{\\c{COLOUR_INACTIVE}}}")
                else:
                    parts.append(f"{{\\c{COLOUR_INACTIVE}}}{word_text}")

            dialogue_text = " ".join(parts)
            ass_lines.append(
                f"Dialogue: 0,{w_s},{w_e},Default,,0,0,0,,{dialogue_text}"
            )

    return "\n".join(ass_lines)


# ─── Whisper singleton ────────────────────────────────────────
# Модель создаётся один раз при первом вызове и переиспользуется.
# Lock защищает от гонки при параллельном preload и первом запросе субтитров.
_whisper_model = None
_whisper_model_name: str | None = None
_whisper_lock = threading.Lock()


def get_subtitles_mode_settings() -> dict:
    """Возвращает настройки subtitle pipeline на основе /settings.

    heavy default (karaoke=True, light=False):
      model=large-v3, word_timestamps=True, karaoke=True

    light mode (light=True):
      model=medium, word_timestamps только если karaoke=True
    """
    karaoke = bool(settings_get("shorts_subtitles_karaoke"))
    light   = bool(settings_get("shorts_subtitles_light"))
    model_name = "medium" if light else os.getenv("WHISPER_MODEL", "large-v3")
    return {
        "model_name":      model_name,
        "karaoke":         karaoke,
        "word_timestamps": karaoke,   # word-level нужен только для karaoke
        "light":           light,
        "gemini_hints":    bool(settings_get("shorts_subtitles_gemini_hints")),
    }


def _reset_whisper_model(device: str | None = None, compute_type: str | None = None):
    """Forcefully resets the singleton so the next call creates a fresh model on the requested device."""
    global _whisper_model, _whisper_model_name
    with _whisper_lock:
        if _whisper_model is not None:
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.synchronize()
            except Exception:
                pass
            try:
                del _whisper_model
            except Exception:
                pass
            _whisper_model = None
        _whisper_model_name = None


def _get_whisper_model(model_size: str | None = None):
    """Возвращает singleton WhisperModel, создаёт при первом вызове или при смене модели."""
    global _whisper_model, _whisper_model_name
    if model_size is None:
        model_size = os.getenv("WHISPER_MODEL", "large-v3")
    with _whisper_lock:
        if _whisper_model is None or _whisper_model_name != model_size:
            if _whisper_model is not None:
                try:
                    del _whisper_model
                except Exception:
                    pass
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                        torch.cuda.synchronize()
                except Exception:
                    pass
            from faster_whisper import WhisperModel as _WM
            _force_cpu = os.getenv("WHISPER_FORCE_CPU", "0").strip().lower() in ("1", "true", "yes", "on")
            _safe_mode = os.getenv("WHISPER_GPU_SAFE_MODE", "0").strip().lower() in ("1", "true", "yes", "on")
            if _force_cpu:
                _whisper_device = "cpu"
                _whisper_compute = "int8"
            else:
                _whisper_device = os.getenv("WHISPER_DEVICE", "cpu").strip().lower()
                if _safe_mode and _whisper_device == "cuda":
                    # Safe mode: int8 on GPU reduces power/heat/vram stress vs float16
                    _whisper_compute = "int8"
                else:
                    _whisper_compute = "float16" if _whisper_device == "cuda" else "int8"
            try:
                _whisper_model = _WM(model_size, device=_whisper_device, compute_type=_whisper_compute)
            except Exception as e:
                logger.warning(f"Failed to load Whisper on {_whisper_device} with {_whisper_compute}. Fallback to CPU/int8. Error: {e}")
                _whisper_device = "cpu"
                _whisper_compute = "int8"
                _whisper_model = _WM(model_size, device=_whisper_device, compute_type=_whisper_compute)
            _whisper_model_name = model_size
            logger.info(f"Whisper model loaded: {model_size} (device={_whisper_device}, compute={_whisper_compute})")
        return _whisper_model


def preload_whisper_if_needed():
    """Устаревшая функция — не вызывается. Используйте _preload_whisper_bg внутри run_bot_async.
    Оставлена для обратной совместимости на случай внешних вызовов.
    """
    import warnings
    warnings.warn(
        "preload_whisper_if_needed() устарела и не используется ботом. "
        "Whisper загружается через _preload_whisper_bg в run_bot_async().",
        DeprecationWarning, stacklevel=2
    )
    if not HAS_FASTER_WHISPER:
        return
    try:
        sub_cfg = get_subtitles_mode_settings()
        _get_whisper_model(sub_cfg["model_name"])
        logger.info(f"Whisper preloaded on startup: {sub_cfg['model_name']}")
    except Exception as e:
        logger.warning(f"Whisper preload failed: {e}")


def _extract_subtitle_hint_terms(ai_data: dict | None, limit: int = 80) -> list[str]:
    """Extract high-value Gemini hints for Whisper without using generated prose as subtitles."""
    if not ai_data:
        return []
    hints: list[str] = []
    for key in ("real_author", "real_event", "real_title"):
        value = normalize_common_typos(str(ai_data.get(key) or "").strip())
        if value:
            hints.append(value)
    for raw in ai_data.get("whisper_hints") or []:
        value = normalize_common_typos(str(raw or "").strip())
        if value:
            hints.append(value)
    td = ai_data.get("terms_data") or {}
    for group in ("concepts", "scripture", "translations", "lexicon_notes"):
        for item in td.get(group, []) or []:
            parts = [p.strip() for p in str(item).split("||") if p.strip()]
            for part in parts[:3]:
                part = normalize_common_typos(part).strip().strip(".")
                if 2 <= len(part) <= 80:
                    hints.append(part)
    out: list[str] = []
    seen: set[str] = set()
    for h in hints:
        key = h.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= limit:
            break
    return out


def build_whisper_initial_prompt(ai_data: dict | None = None, *, use_gemini_hints: bool = True) -> str:
    """Build Whisper initial_prompt from Gemini vocabulary hints.

    We do NOT use Gemini-generated text as subtitles. Whisper remains the timing
    source; Gemini only supplies names/terms/scripture vocabulary so ASR writes
    МакАртур, Исаия 53, Раб Иеговы, etc. correctly.
    """
    base = "Проповедь на русском языке. Сохраняй богословские термины, имена и ссылки на Писание точно."
    if not use_gemini_hints:
        return base
    hints = _extract_subtitle_hint_terms(ai_data)
    if not hints:
        return base
    return base + " Ключевые слова и имена: " + ", ".join(hints[:80]) + "."


def _polish_subtitle_text(text: str) -> str:
    text = normalize_common_typos(str(text or "").strip())
    text = text.replace(" ,", ",").replace(" .", ".").replace(" ?", "?").replace(" !", "!")
    return " ".join(text.split())


async def transcribe_short_clip(video_path: Path, ai_data: dict = None) -> list[dict]:
    """
    Транскрибирует аудио клипа через faster-whisper.
    Возвращает сегменты [{"start", "end", "text"}] или [] если нет модели/ошибка.
    Время в сегментах — относительно начала видео (0 = старт клипа).
    """
    # ── Backend check ──────────────────────────────────────────
    if not HAS_FASTER_WHISPER:
        logger.warning(
            "Subtitles: faster-whisper не установлен — субтитры недоступны. "
            "Установите: pip install faster-whisper"
        )
        return []

    # ── File check ─────────────────────────────────────────────
    if not video_path.exists():
        logger.warning(f"Subtitles: файл для транскрипции не найден: {video_path}")
        return []
    video_size_mb = video_path.stat().st_size / (1024 * 1024)
    logger.info(
        f"Subtitles: транскрибирую {video_path.name} "
        f"({video_size_mb:.1f} MB) через faster-whisper"
    )

    wav_path: Path | None = None
    try:
        import tempfile
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("Subtitles: ffmpeg не найден — невозможно извлечь аудио")
            return []

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)

        # Извлекаем аудио в WAV 16kHz mono
        cmd = [
            ffmpeg, "-i", str(video_path),
            "-ar", "16000", "-ac", "1", "-f", "wav", "-y", str(wav_path),
        ]
        loop = asyncio.get_running_loop()
        proc = await loop.run_in_executor(
            None, lambda: subprocess.run(cmd, capture_output=True, timeout=120)
        )

        if proc.returncode != 0:
            logger.warning(
                f"Subtitles: ffmpeg извлечение аудио упало (code={proc.returncode}): "
                f"{(proc.stderr or b'')[-300:]}"
            )
            wav_path.unlink(missing_ok=True)
            return []

        # Проверяем что WAV не пустой
        wav_size = wav_path.stat().st_size if wav_path.exists() else 0
        if wav_size < 1024:
            logger.warning(
                f"Subtitles: WAV файл слишком мал или пустой ({wav_size} байт) — "
                "возможно видео без аудиодорожки"
            )
            wav_path.unlink(missing_ok=True)
            return []

        logger.info(f"Subtitles: WAV извлечён ({wav_size / 1024:.0f} KB), запускаю Whisper...")

        subtitle_cfg = get_subtitles_mode_settings()
        model_size   = subtitle_cfg["model_name"]
        word_ts      = subtitle_cfg["word_timestamps"]
        logger.info(
            f"Subtitles mode: model={model_size} "
            f"karaoke={subtitle_cfg['karaoke']} "
            f"word_ts={word_ts} "
            f"light={subtitle_cfg['light']}"
        )
        _wav_path_for_thread = wav_path  # явная копия для closure

        # Gemini is used only as vocabulary/context hints for Whisper, not as
        # subtitle text. Timing and transcript still come from ASR.
        _whisper_initial_prompt = build_whisper_initial_prompt(
            ai_data,
            use_gemini_hints=bool(subtitle_cfg.get("gemini_hints", True)),
        )
        logger.info("Subtitles: initial_prompt chars=%d gemini_hints=%s", len(_whisper_initial_prompt), subtitle_cfg.get("gemini_hints", True))
        _initial_prompt_for_thread = _whisper_initial_prompt

        def _run_whisper():
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    torch.cuda.synchronize()
            except Exception:
                pass
            model = _get_whisper_model(model_size)
            segs, info = model.transcribe(
                str(_wav_path_for_thread),
                language="ru",
                initial_prompt=_initial_prompt_for_thread,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                word_timestamps=word_ts,
            )
            # segs — генератор, материализуем его здесь же, в том же потоке
            result = [
                {
                    "start": s.start,
                    "end":   s.end,
                    "text":  _polish_subtitle_text(s.text),
                    "words": [
                        {"word": _polish_subtitle_text(w.word), "start": w.start, "end": w.end}
                        for w in (s.words or [])
                    ],
                }
                for s in segs
            ]
            return result, getattr(info, "duration", 0), getattr(info, "language", "?"), getattr(info, "language_probability", 1.0)

        # AUDIT R29: сериализуем Whisper — параллельные видео не грузят CPU
        # тремя large-v3 транскрипциями разом (иначе каждая тянется 3-8 мин).
        from core.resource_scheduler import scheduler as _sched
        async with _sched.whisper:
            segments, audio_duration, detected_lang, lang_prob = await loop.run_in_executor(None, _run_whisper)
        wav_path.unlink(missing_ok=True)
        wav_path = None

        # Примечание: _fill_timing_gaps и _merge_hyphenated_particles применяются позже,
        # в _generate_ass_from_segments, к полному плоскому списку слов (cross-segment).
        # Применять их здесь per-segment нет смысла: cross-segment дыры не обрабатываются.

        logger.info(
            f"Subtitles: Whisper detected lang={detected_lang} "
            f"confidence={lang_prob:.2f}, model={model_size}"
        )

        # Если уверенность низкая — скорее всего синхронный перевод или смешанный язык
        # Субтитры в таком случае будут мусором
        if lang_prob < 0.4:
            logger.info(
                f"Subtitles: низкая уверенность в языке ({lang_prob:.2f}) — "
                "вероятно синхронный перевод, субтитры пропущены"
            )
            return []

        non_empty = [s for s in segments if s.get("text")]
        logger.info(
            f"Subtitles: Whisper вернул {len(segments)} сегментов "
            f"({len(non_empty)} непустых) из {video_path.name} "
            f"(audio_duration={audio_duration:.1f}s, model={model_size})"
        )
        if not non_empty:
            logger.info(
                "Subtitles: все сегменты пустые — возможно тишина, музыка или "
                "неподходящий язык (выставлен 'ru')"
            )
        return non_empty

    except Exception as e:
        logger.warning(f"Subtitles transcribe error ({type(e).__name__}): {e}")
        if wav_path is not None:
            try:
                wav_path.unlink(missing_ok=True)
            except Exception:
                pass
        return []


async def burn_subtitles_into_short(
    input_path: Path,
    output_path: Path,
    segments: list[dict],
) -> bool:
    """
    Вшивает субтитры в клип через ffmpeg ASS burn-in.
    Стиль: белый текст, полупрозрачная тёмная подложка, нижняя safe-zone.
    При ошибке исходный файл остаётся нетронутым.
    """
    try:
        import tempfile
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg or not input_path.exists() or not segments:
            return False
        subtitle_cfg = get_subtitles_mode_settings()
        ass_content  = _generate_ass_from_segments(segments, karaoke=subtitle_cfg["karaoke"])
        with tempfile.NamedTemporaryFile(
            suffix=".ass", mode="w", encoding="utf-8", delete=False
        ) as tmp:
            tmp.write(ass_content)
            ass_path = Path(tmp.name)
        ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
        _enc, _quality, _preset = _get_video_encoder()
        _hwaccel = []  # hwaccel cuda убран: CPU-фильтры несовместимы с CUDA decode
        cmd = [
            ffmpeg, *_hwaccel, "-i", str(input_path),
            "-vf", f"subtitles='{ass_escaped}'",
            "-c:v", _enc, *_preset, *_quality,
            "-c:a", "copy", "-movflags", "+faststart", "-y", str(output_path),
        ]
        loop = asyncio.get_running_loop()
        # AUDIT R29b: burn-in субтитров — самый длинный NVENC-проход пайплайна;
        # серилизуем через GPU-семафор, чтобы не драться за карту с рендером/
        # постобработкой параллельных видео.
        from core.resource_scheduler import scheduler as _sched
        async with _sched.gpu_render:
            proc = await loop.run_in_executor(
                None, lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            )
        ass_path.unlink(missing_ok=True)
        if proc.returncode != 0:
            logger.warning(f"burn_subtitles ffmpeg error: {(proc.stderr or '')[-500:]}")
            return False
        if not output_path.exists() or output_path.stat().st_size == 0:
            return False
        logger.info(
            f"Subtitles burned: {output_path.name} "
            f"({output_path.stat().st_size / 1024 / 1024:.1f}MB)"
        )
        return True
    except Exception as e:
        logger.warning(f"burn_subtitles_into_short error: {type(e).__name__}: {e}")
        return False


# ── Постер с заголовком для Shorts ───────────────────────────

def _wrap_poster_title(title: str, max_chars: int = 22) -> list[str]:
    """Разбивает заголовок постера на строки (макс. 3), не разрывая слова."""
    words = title.split()
    lines: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        add_len = len(word) + (1 if current else 0)
        if current_len + add_len <= max_chars:
            current.append(word)
            current_len += add_len
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
            current_len = len(word)
    if current:
        lines.append(" ".join(current))
    if len(lines) > 3:
        lines = lines[:2] + [" ".join(lines[2:])]
    return lines[:3]


async def create_short_title_poster(
    video_path: Path,
    poster_path: Path,
    title: str,
    clip_duration_seconds: float,
) -> bool:
    """
    Создаёт стильный постер для short (PIL):
    - кадр из видео на 25% длины клипа
    - мягкое градиентное затемнение нижней части
    - заголовок крупным белым шрифтом с тёмной тенью

    Требует Pillow (HAS_PILLOW). При любой ошибке возвращает False.
    """
    if not HAS_PILLOW:
        return False
    frame_path: Path | None = None  # объявляем до try для cleanup в except
    try:
        import tempfile
        from PIL import Image, ImageDraw, ImageFont

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg or not video_path.exists():
            return False

        seek_time = max(1.0, clip_duration_seconds * 0.25)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            frame_path = Path(tmp.name)

        cmd = [
            ffmpeg, "-ss", str(seek_time), "-i", str(video_path),
            "-vframes", "1", "-q:v", "2", "-y", str(frame_path),
        ]
        loop = asyncio.get_running_loop()
        proc = await loop.run_in_executor(
            None, lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        )
        if proc.returncode != 0 or not frame_path.exists() or frame_path.stat().st_size == 0:
            frame_path.unlink(missing_ok=True)
            return False

        def _draw_poster() -> bool:
            try:
                with Image.open(frame_path) as base:
                    img = base.convert("RGBA")
                W, H = img.size

                # Градиентное затемнение нижних 55% — мягкий, не агрессивный
                overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
                draw_ov = ImageDraw.Draw(overlay)
                grad_top = int(H * 0.38)
                steps = 50
                for step in range(steps):
                    alpha = int((step / steps) * 175)  # макс. ~69% — не давит на картинку
                    y0 = grad_top + int((H - grad_top) * step / steps)
                    y1 = grad_top + int((H - grad_top) * (step + 1) / steps)
                    draw_ov.rectangle([(0, y0), (W, y1)], fill=(0, 0, 0, alpha))
                img = Image.alpha_composite(img, overlay)
                draw = ImageDraw.Draw(img)

                # Шрифт: приоритет Noto Sans / Inter / Montserrat (SemiBold/Bold),
                # чистые sans-serif, хорошо работают с русским.
                # Fallback: DejaVu → Liberation → FreeSans → default.
                font_size = max(56, W // 13)  # ~56px при 720px, чуть крупнее прежнего
                font = None
                for fp in [
                    # Noto Sans — лучший выбор для русского
                    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
                    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
                    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
                    # Inter (если установлен)
                    "/usr/share/fonts/truetype/inter/Inter-SemiBold.ttf",
                    "/usr/local/share/fonts/Inter-SemiBold.ttf",
                    # Montserrat (если установлен)
                    "/usr/share/fonts/truetype/montserrat/Montserrat-SemiBold.ttf",
                    # Liberation Sans — хороший fallback, похож на Arial
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
                    # DejaVu — широко доступен
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    # FreeSans
                    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
                ]:
                    if Path(fp).exists():
                        try:
                            font = ImageFont.truetype(fp, font_size)
                            break
                        except Exception:
                            continue
                if font is None:
                    font = ImageFont.load_default()

                lines = _wrap_poster_title(title)
                line_h = int(font_size * 1.30)   # чуть больший межстрочный интервал
                block_h = len(lines) * line_h
                # Safe-zone: 13% от нижнего края (≈167px при H=1280) — под интерфейс Shorts
                safe_margin = int(H * 0.13)
                text_top = H - safe_margin - block_h

                # Тень: многослойная мягкая — 3 слоя с нарастающей прозрачностью
                # Это даёт ощущение «глубины» без грубой жёсткой обводки
                shadow_layers = [
                    (3, 3, (0, 0, 0, 100)),   # дальний слой — очень мягкий
                    (2, 2, (0, 0, 0, 160)),   # средний
                    (1, 1, (0, 0, 0, 200)),   # близкий
                ]

                for li, line in enumerate(lines):
                    y = text_top + li * line_h
                    bbox = draw.textbbox((0, 0), line, font=font)
                    x = (W - (bbox[2] - bbox[0])) // 2

                    # Рисуем тень (все слои)
                    for sx, sy, sc in shadow_layers:
                        draw.text((x + sx, y + sy), line, font=font, fill=sc)

                    # Тонкая тёмная обводка (1px вокруг — читаемость на светлом фоне)
                    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 180))

                    # Основной белый текст
                    draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))

                img.convert("RGB").save(str(poster_path), "JPEG", quality=88)
                return True
            except Exception as e:
                logger.warning(f"_draw_poster error: {e}")
                return False

        result = await loop.run_in_executor(None, _draw_poster)
        frame_path.unlink(missing_ok=True)

        if result and poster_path.exists() and poster_path.stat().st_size > 0:
            logger.info(f"Title poster: {poster_path.name}")
            return True
        return False

    except Exception as e:
        logger.warning(f"create_short_title_poster error: {type(e).__name__}: {e}")
        if frame_path is not None:
            try:
                frame_path.unlink(missing_ok=True)
            except Exception:
                pass
        return False


async def create_short_snapshot(
    video_path: Path,
    snapshot_path: Path,
    clip_duration_seconds: float,
) -> bool:
    """
    Извлекает один кадр из short-клипа в виде JPEG-постера.
    Кадр берётся на 30% длины клипа — минует черноту в начале, но ещё до середины.

    Возвращает True при успехе, False при ошибке.
    """
    try:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return False
        if not video_path.exists():
            return False

        seek_time = max(1.0, clip_duration_seconds * 0.30)
        cmd = [
            ffmpeg,
            "-ss", str(seek_time),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",       # JPEG quality 2 = высокое качество
            "-y",
            str(snapshot_path),
        ]
        proc = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=60),
        )
        if proc.returncode != 0 or not snapshot_path.exists() or snapshot_path.stat().st_size == 0:
            logger.warning(f"create_short_snapshot: не удалось извлечь кадр из {video_path.name}")
            return False

        logger.info(f"Snapshot: {snapshot_path.name} (t={seek_time:.1f}s)")
        return True

    except Exception as e:
        logger.warning(f"create_short_snapshot error: {type(e).__name__}: {e}")
        return False


def _build_links_block(yt_url: str = "", rutube_url: str = "", vk_url: str = "") -> str:
    """Строит блок ссылок с эмодзи для caption. Каждая ссылка на своей строке."""
    lines = []
    if yt_url:
        _yt = get_youtube_video_url(yt_url)
        lines.append(f'<tg-emoji emoji-id="5463206079913533096">🎥</tg-emoji> <a href="{_yt}">YouTube</a>')
    if rutube_url:
        lines.append(f'<tg-emoji emoji-id="5321388265549373570">🎬</tg-emoji> <a href="{rutube_url}">RuTube</a>')
    if vk_url:
        lines.append(f'<tg-emoji emoji-id="5278229754099540071">🎦</tg-emoji> <a href="{vk_url}">VK</a>')
    if not lines:
        return ""
    return "<b>Полное видео:</b>\n" + "\n".join(lines)


_SHORT_TITLE_SPACED_DASH_RE = re.compile(r"(?<=\S)\s+(?:-|–|—)\s+(?=\S)")


def _prepare_short_hook(hook: str, author_label: str = "") -> str:
    """Normalize one Shorts headline without touching hyphens inside words.

    Channel contract:
    - an internal semantic pause uses `` — ``;
    - the outer headline/author boundary is added later as `` - ``.

    Strip only an exact trailing author suffix before normalizing internal
    spaced dash variants. This prevents duplicate authors without removing a
    name that is legitimately part of the headline itself.
    """
    title = re.sub(r"\s+", " ", str(hook or "")).strip()
    author = re.sub(r"\s+", " ", str(author_label or "")).strip()
    if title and author:
        trailing_author = re.compile(
            rf"\s+(?:-|–|—)\s+{re.escape(author)}(?:[.!?])?$",
            re.IGNORECASE,
        )
        title = trailing_author.sub("", title).rstrip()
    return _SHORT_TITLE_SPACED_DASH_RE.sub(" — ", title)


def build_short_caption(
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
    Строит подпись для YouTube Shorts.
    Компактный стиль: заголовок - Автор, ссылки с эмодзи, хэштеги.
    """
    kind         = (candidate.get("kind") or "").strip()
    hook         = (candidate.get("hook") or candidate.get("title") or "").strip()
    tags         = candidate.get("hashtags") or []
    author_label = real_author or performer or ""

    prepared_hook = _prepare_short_hook(hook, author_label)
    hook_tc = html_mod.escape(title_case_fragment(prepared_hook)) if prepared_hook else ""
    if kind == "quote":
        hook_tc = f"«{hook_tc}»" if hook_tc and not hook_tc.startswith("«") else hook_tc

    author_safe = html_mod.escape(author_label) if author_label else ""

    if hook_tc and author_safe:
        # Канальный контракт: внутренний смысловой разрыв = « — »,
        # граница «заголовок - автор» = обычный дефис с пробелами.
        first_line = f"{hook_tc} - {author_safe}"
    else:
        first_line = hook_tc or author_safe

    links_block = _build_links_block(yt_url, rutube_url, vk_url)
    # PATCH V2 FIX: убираем # если Gemini добавил вопреки инструкции → иначе ##тег
    if tags:
        _clean_tags = [str(t).strip().lstrip('#') for t in tags[:4] if str(t).strip()]
        tags_line = " ".join(f"#{t}" for t in _clean_tags if t)
    else:
        tags_line = ""

    parts = [p for p in [first_line, links_block, tags_line] if p]
    return "\n\n".join(parts)



# ─── Clips MVP (длинные фрагменты 5–15 мин) ──────────────────

