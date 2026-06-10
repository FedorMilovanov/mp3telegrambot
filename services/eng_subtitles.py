import os
import re
import asyncio
import json
import logging
import shutil
import subprocess
from pathlib import Path

from core.globals import GEMINI_CLIENTS
from core.database import GEMINI_MODEL

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


async def create_gemini_subtitles(video_url: str, workdir: Path) -> Path | None:
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

    # 1. Скачиваем аудио
    yt_dlp = shutil.which("yt-dlp")
    if not yt_dlp:
        raise RuntimeError("yt-dlp not found")

    cmd = [yt_dlp, "--format", "bestaudio/best", "--output", f"{audio_path}.%(ext)s", video_url]
    logger.info(f"[EngSubtitles] Скачиваем аудио: {' '.join(cmd)}")

    def _run_cmd(t):
        kwargs = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return subprocess.run(cmd, timeout=t, **kwargs)

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
        "[EngSubtitles] Whisper CPU: model=large-v3, int8, beam_size=1, audio=%.1fs",
        audio_duration,
    )

    def _run_whisper():
        model = _get_whisper_model()
        segs_gen, _ = model.transcribe(
            str(actual_audio),
            language="en",
            beam_size=1,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
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

async def download_original_video(video_url: str, workdir: Path) -> Path:
    """Скачивает оригинальное видео в формате mp4."""
    workdir.mkdir(parents=True, exist_ok=True)
    video_path = workdir / "original_video.mp4"
    
    yt_dlp = shutil.which("yt-dlp")
    cmd = [
        yt_dlp, "--format", "bestvideo+bestaudio/best",
        "--merge-output-format", "mp4",
        "--output", str(video_path), video_url
    ]
    logger.info("[EngSubtitles] Скачиваем резервное оригинальное видео...")

    def _run_cmd(t):
        kwargs = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return subprocess.run(cmd, timeout=t, **kwargs)

    loop = asyncio.get_running_loop()
    proc = await loop.run_in_executor(None, lambda: _run_cmd(900))
    
    actual_video = None
    for file in workdir.glob("original_video.*"):
        actual_video = file
        break
        
    if not actual_video or not actual_video.exists():
        raise RuntimeError(f"Не удалось скачать оригинальное видео. stderr: {proc.stderr[-500:] if proc.stderr else ''}")
        
    return actual_video

async def merge_subtitles(video_path: Path, srt_path: Path, is_fallback: bool = False) -> Path:
    """Вшивает SRT-субтитры в видео-файл с флагом по умолчанию."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning("[EngSubtitles] ffmpeg не найден")
        return video_path
        
    output_path = video_path.with_suffix(".merged.mp4")
    
    cmd = [
        ffmpeg, "-i", str(video_path), "-i", str(srt_path),
        "-map", "0", "-map", "1",
        "-c", "copy", 
        "-c:s", "mov_text", 
        "-metadata:s:s:0", "language=rus",
        "-disposition:s:0", "default",
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
