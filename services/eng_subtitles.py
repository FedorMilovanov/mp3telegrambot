import os
import re
import asyncio
import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from core.globals import GEMINI_CLIENTS
from core.database import GEMINI_MODEL
from services.ffmpeg import YTDLP_BASE_ARGS

logger = logging.getLogger(__name__)

# ── Hard limits for English subtitle pipeline ───────────────────
_MAX_SUBTITLE_AUDIO_SECONDS = 180  # 3 minutes: longer than Shorts, skip entirely

async def _translate_chunk_with_retry(chunk_segs, prev_context=""):
    """Translates a chunk of segments with retries and JSON enforcement across multiple Gemini keys."""
    if not GEMINI_CLIENTS:
        return None

    prompt_lines = [
        "You are an expert subtitle translator. Translate these English subtitle segments into Russian.",
        "Rules:",
        "1. Preserve exact ID numbers from the input.",
        "2. Ensure theological, technical, and colloquial terms are accurate and sound natural in Russian.",
        "3. Keep translations concise to fit nicely on a video screen (merge short stutters smoothly).",
        "4. Output strictly a JSON object where keys are the IDs (as strings) and values are the translated Russian text.",
        "5. Do NOT output any markdown, explanations, or text outside the JSON object.",
    ]
    if prev_context:
        prompt_lines.append(f"\nFor context, the preceding translated segment was: \"{prev_context}\"")

    prompt_lines.append("\nSegments to translate:")
    for sid, text in chunk_segs:
        prompt_lines.append(f"ID: {sid} | Text: {text}")

    prompt = "\n".join(prompt_lines)

    for attempt in range(max(3, len(GEMINI_CLIENTS))):
        client = GEMINI_CLIENTS[attempt % len(GEMINI_CLIENTS)]
        try:
            from google.genai import types
            response = await client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                )
            )
            text = response.text.strip()
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            data = json.loads(text)
            return data
        except Exception as e:
            logger.warning(f"[EngSubtitles] Chunk translation attempt {attempt+1} failed: {e}")
            await asyncio.sleep(2)

    return None


def _get_audio_duration(path: Path) -> float:
    try:
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=30
            )
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0.0


async def create_gemini_subtitles(video_url: str, workdir: Path, known_duration: int = 0, lang: str = "") -> Optional[Path]:
    from services.shorts_video import _get_whisper_model
    """
    Скачивает оригинальное аудио, прогоняет faster-whisper (CPU-only),
    переводит через Gemini (JSON), сохраняет в SRT.

    Returns:
        Path к SRT-файлу, или None если аудио слишком длинное (>3 мин).
    """
    workdir.mkdir(parents=True, exist_ok=True)
    audio_path = workdir / "original_audio"
    srt_path = workdir / "gemini_subs.srt"

    # 0. PERF FIX 2026-06-10 (live log): длительность известна из метаданных
    # ДО скачивания — раньше бот скачивал ВСЁ аудио 23-мин ролика (~1.5 мин
    # yt-dlp) и только потом отбрасывал его по лимиту 180с.
    if known_duration and known_duration > _MAX_SUBTITLE_AUDIO_SECONDS:
        logger.info(
            "[EngSubtitles] Видео %dс > %dс (из метаданных) — субтитры пропущены без скачивания.",
            known_duration, _MAX_SUBTITLE_AUDIO_SECONDS,
        )
        return None

    # 1. Скачиваем аудио
    cmd = YTDLP_BASE_ARGS + ["--format", "bestaudio/best", "--output", f"{audio_path}.%(ext)s", video_url]
    logger.info(f"[EngSubtitles] Скачиваем аудио: {' '.join(cmd)}")

    def _run_cmd(t):
        kwargs = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
        # Check if cmd[0] is sys.executable or a standalone binary
        _shell = False
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            # If we use python -m yt_dlp, it's safer
            if "yt-dlp" in cmd[0].lower() and not cmd[0].lower().endswith(".exe"):
                 _shell = True
        
        return subprocess.run(cmd, timeout=t, shell=_shell, **kwargs)

    loop = asyncio.get_running_loop()
    proc = await loop.run_in_executor(None, lambda: _run_cmd(300))

    actual_audio = None
    for file in workdir.glob("original_audio.*"):
        if file.suffix != ".srt":
            actual_audio = file
            break

    if not actual_audio or not actual_audio.exists():
        raise RuntimeError(f"Не удалось скачать аудио. stderr: {proc.stderr[-500:] if proc.stderr else ''}")

    # 2. Длительность — если больше лимита, субтитры не делаем
    audio_duration = _get_audio_duration(actual_audio)
    if audio_duration > _MAX_SUBTITLE_AUDIO_SECONDS:
        logger.info(
            "[EngSubtitles] Аудио слишком длинное (%.1f сек > %d сек) — субтитры пропущены.",
            audio_duration, _MAX_SUBTITLE_AUDIO_SECONDS,
        )
        return None

    # 3. Whisper — CPU-only, large-v3, int8, beam_size=1
    logger.info(
        "[EngSubtitles] Whisper CPU: model=large-v3, int8, beam_size=1, audio=%.1fs, lang=%s",
        audio_duration, lang or "auto",
    )

    def _run_whisper():
        model = _get_whisper_model()
        # Если язык известен (en, ru, de...) — форсируем, чтобы Whisper не ошибся
        # на акцентах. yt-dlp даёт ISO-коды, Whisper их понимает.
        transcribe_args = {
            "language": lang if lang else "en",
            "beam_size": 1,
            "vad_filter": True,
            "vad_parameters": {"min_silence_duration_ms": 500},
        }
        segs_gen, _ = model.transcribe(str(actual_audio), **transcribe_args)
        return list(segs_gen)

    segments = await loop.run_in_executor(None, _run_whisper)

    if not segments:
        logger.warning("[EngSubtitles] Whisper не нашел речь.")
        return None

    # 4. Перевод Gemini батчами
    translated_segments = {}

    if not GEMINI_CLIENTS:
        logger.warning("[EngSubtitles] Gemini API не настроен, оставляем английский.")
        translated_segments = {str(i): seg.text.strip() for i, seg in enumerate(segments, 1)}
    else:
        logger.info("[EngSubtitles] Переводим субтитры через Gemini (chunked JSON)...")
        CHUNK_SIZE = 50
        chunks = []

        current_chunk = []
        for i, seg in enumerate(segments, 1):
            current_chunk.append((i, seg.text.strip()))
            if len(current_chunk) >= CHUNK_SIZE:
                chunks.append(current_chunk)
                current_chunk = []
        if current_chunk:
            chunks.append(current_chunk)

        prev_context = ""
        for idx, chunk in enumerate(chunks):
            logger.info(f"[EngSubtitles] Перевод чанка {idx+1}/{len(chunks)}...")
            data = await _translate_chunk_with_retry(chunk, prev_context)

            if data and isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, str) and v.strip():
                        translated_segments[str(k)] = v.strip()
                    elif isinstance(v, (int, float)):
                        translated_segments[str(k)] = str(v)
                if chunk:
                    last_id = chunk[-1][0]
                    prev_context = data.get(str(last_id), "")
            else:
                logger.warning(f"[EngSubtitles] Чанк {idx+1} не удался. Используем оригинал.")
                for sid, text in chunk:
                    translated_segments[str(sid)] = text

    # 5. Запись SRT
    def _to_srt_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds - int(seconds)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            text = translated_segments.get(str(i), seg.text.strip())
            text = text.replace("\n", " ").strip()

            f.write(
                f"{i}\n"
                f"{_to_srt_time(seg.start)} --> {_to_srt_time(seg.end)}\n"
                f"{text}\n\n"
            )

    logger.info(f"[EngSubtitles] Субтитры созданы: {srt_path}")
    return srt_path

def _has_video_stream(path: Path) -> bool:
    """True если файл реально содержит видеопоток.

    yt-dlp иногда оставляет рядом `original_video.f140.m4a` (audio-only). Старый
    reuse blindly хватал первый `original_video.*`, и LiveDub-mix падал на
    `-map 0:v`: «Stream map '' matches no streams». Поэтому проверяем ffprobe.
    """
    path = Path(path)
    if not path.exists() or path.stat().st_size <= 100 * 1024:
        return False
    try:
        from services.livedub_mix import has_video_stream
        return has_video_stream(path)
    except Exception as e:
        logger.debug("[EngSubtitles] video-stream check failed for %s: %s", path, e)
        return path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov", ".m4v"}


async def download_original_video(video_url: str, workdir: Path) -> Path:
    """Скачивает оригинальное видео в формате mp4."""
    workdir.mkdir(parents=True, exist_ok=True)
    video_path = workdir / "original_video.mp4"

    # Реюз: pro-микс мог уже скачать оригинал в эту же папку. Но audio-only
    # артефакты (original_video.f140.m4a) нельзя отдавать как видео.
    for existing in sorted(workdir.glob("original_video.*")):
        if existing.is_file() and _has_video_stream(existing):
            logger.info(f"[EngSubtitles] Оригинал уже скачан: {existing.name} — реюз")
            return existing
        if existing.is_file() and existing.stat().st_size > 100 * 1024:
            logger.info(f"[EngSubtitles] {existing.name} без видеопотока — не реюзаю для DUB")

    
    # FIX: используем YTDLP_BASE_ARGS (cookies, js-runtime, общие настройки) —
    # без них YouTube часто отвечает 403 Forbidden (та же причина, что в cabbac8).
    # Ограничиваем высоту 1080p: для дубляжа выше не нужно, а вес/время скачивания
    # на длинных роликах падают в разы.
    cmd = YTDLP_BASE_ARGS + [
        "--format", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "--merge-output-format", "mp4",
        "--output", str(video_path), video_url
    ]
    logger.info("[EngSubtitles] Скачиваем оригинальное видео (<=1080p, base args)...")

    def _run_cmd(t):
        kwargs = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return subprocess.run(cmd, timeout=t, **kwargs)

    loop = asyncio.get_running_loop()
    proc = await loop.run_in_executor(None, lambda: _run_cmd(900))
    
    actual_video = None
    for file in sorted(workdir.glob("original_video.*")):
        if _has_video_stream(file):
            actual_video = file
            break
        if file.is_file() and file.stat().st_size > 100 * 1024:
            logger.info(f"[EngSubtitles] после yt-dlp {file.name} без видеопотока — пропускаю")
        
    if not actual_video or not actual_video.exists():
        raise RuntimeError(f"Не удалось скачать оригинальное видео с видеопотоком. stderr: {proc.stderr[-500:] if proc.stderr else ''}")
        
    return actual_video

async def _burn_subtitles(video_path: Path, srt_path: Path, ffmpeg: str) -> Optional[Path]:
    """Прожигает SRT в кадр. YouTube-стиль: белый текст на полупрозрачной
    подложке (BorderStyle=3) — читается на любом фоне."""
    output_path = video_path.with_suffix(".sub.mp4")
    # ffmpeg subtitles= требует экранирования спецсимволов пути
    srt_arg = str(srt_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    style = "FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=3,Outline=1,Shadow=0,MarginV=20"
    # NVENC если доступен (у пользователя рабочий ASIC) — прожиг быстрее
    # и без нагрузки на CPU; качество формулы p5+hq уже выверено.
    from services.ffmpeg import _get_video_encoder
    _enc, _q, _pr = _get_video_encoder()
    cmd = [
        ffmpeg, "-i", str(video_path),
        "-vf", f"subtitles='{srt_arg}':force_style='{style}'",
        "-c:v", _enc, *_q, *_pr,
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-y", str(output_path),
    ]
    def _run(t):
        kwargs = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return subprocess.run(cmd, timeout=t, **kwargs)
    loop = asyncio.get_running_loop()
    try:
        proc = await loop.run_in_executor(None, lambda: _run(600))
    except Exception as e:
        logger.warning("[EngSubtitles] hardsub exception: %s", e)
        return None
    if proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 10240:
        logger.info("[EngSubtitles] Субтитры прожжены в кадр (hardsub)")
        return output_path
    logger.warning("[EngSubtitles] hardsub rc=%s: %s", proc.returncode,
                   (proc.stderr or "")[-300:])
    return None


async def merge_subtitles(video_path: Path, srt_path: Path, is_fallback: bool = False) -> Path:
    """Добавляет русские субтитры в видео.

    FIX 2026-06-10: Telegram-плеер (iOS/Android/Desktop) НЕ отображает
    встроенные mov_text-дорожки — софт-сабы были видны только при
    скачивании файла во внешний плеер. Для коротких роликов (<3 мин,
    только они и получают сабы) ПРОЖИГАЕМ субтитры в кадр (hardsub):
    видны везде. Стиль — читабельный YouTube-подобный (полупрозрачная
    подложка). Отключение прожига: LIVEDUB_HARDSUB=0 (вернётся mov_text).
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning("[EngSubtitles] ffmpeg не найден")
        return video_path

    if (os.getenv("LIVEDUB_HARDSUB", "1").strip().lower() not in {"0", "false", "no", "off"}):
        burned = await _burn_subtitles(video_path, srt_path, ffmpeg)
        if burned is not None:
            return burned
        logger.warning("[EngSubtitles] hardsub не удался — fallback на mov_text")
        
    output_path = video_path.with_suffix(".merged.mp4")
    
    cmd = [
        ffmpeg, "-i", str(video_path), "-i", str(srt_path),
        "-map", "0", "-map", "1",
        "-c", "copy", 
        "-c:s", "mov_text", 
        "-metadata:s:s:0", "language=rus",
        "-disposition:s:0", "default",
        "-movflags", "+faststart",
        "-y", str(output_path)
    ]
    
    logger.info(f"[EngSubtitles] Склейка субтитров: {' '.join(cmd)}")

    def _run_cmd(t):
        kwargs = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return subprocess.run(cmd, timeout=t, **kwargs)

    loop = asyncio.get_running_loop()
    proc = await loop.run_in_executor(None, lambda: _run_cmd(300))
    
    if output_path.exists() and output_path.stat().st_size > 1000:
        return output_path
    
    logger.warning(f"[EngSubtitles] Ошибка склейки субтитров (default flag). Пробуем без default. stderr: {proc.stderr[-300:] if proc.stderr else ''}")
    
    # Резервный fallback - без disposition и без mov_text если контейнер конфликтует
    cmd_fallback = [
        ffmpeg, "-i", str(video_path), "-i", str(srt_path),
        "-c", "copy", "-c:s", "mov_text", "-metadata:s:s:0", "language=rus",
        "-y", str(output_path)
    ]

    def _run_cmd_fallback(t):
        kwargs = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return subprocess.run(cmd_fallback, timeout=t, **kwargs)

    proc2 = await loop.run_in_executor(None, lambda: _run_cmd_fallback(300))
    if output_path.exists() and output_path.stat().st_size > 1000:
        return output_path
        
    logger.error(f"[EngSubtitles] Полный отказ склейки. stderr: {proc2.stderr[-300:] if proc2.stderr else ''}")
    return video_path
