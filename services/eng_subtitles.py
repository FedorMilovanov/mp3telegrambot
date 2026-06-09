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
    
    # Retry across multiple clients if available
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
            # Убираем markdown обертку, которую иногда возвращает Gemini даже при response_mime_type="application/json"
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            
            data = json.loads(text)
            return data
        except Exception as e:
            logger.warning(f"[EngSubtitles] Chunk translation attempt {attempt+1} failed: {e}")
            await asyncio.sleep(2)
            
    return None

async def create_gemini_subtitles(video_url: str, workdir: Path) -> Path:
    """
    Скачивает оригинальное аудио, прогоняет faster-whisper, 
    умно батчит сегменты, переводит через Gemini (JSON), сохраняет в SRT.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    audio_path = workdir / "original_audio"  # let yt-dlp determine extension
    srt_path = workdir / "gemini_subs.srt"

    # 1. Скачиваем аудио (быстро)
    yt_dlp = shutil.which("yt-dlp")
    if not yt_dlp:
        raise RuntimeError("yt-dlp not found")
    
    cmd = [yt_dlp, "--format", "bestaudio/best", "--output", f"{audio_path}.%(ext)s", video_url]
    logger.info(f"[EngSubtitles] Скачиваем аудио для транскрипции: {' '.join(cmd)}")
    
    loop = asyncio.get_running_loop()
    proc = await loop.run_in_executor(None, lambda: subprocess.run(cmd, capture_output=True, timeout=300))
    
    actual_audio = None
    for file in workdir.glob("original_audio.*"):
        if file.suffix != ".srt":
            actual_audio = file
            break
            
    if not actual_audio or not actual_audio.exists():
        raise RuntimeError(f"Не удалось скачать аудио. stderr: {proc.stderr[-500:] if proc.stderr else ''}")

    # 2. Whisper
    try:
        from services.shorts_video import _get_whisper_model
    except ImportError:
        raise RuntimeError("Не удалось импортировать _get_whisper_model")
    
    logger.info("[EngSubtitles] Запускаем Whisper (глобальная модель)...")
    def run_whisper():
        model = _get_whisper_model()
        segs_gen, _ = model.transcribe(str(actual_audio), language="en", beam_size=5)
        return list(segs_gen)

    segments = await loop.run_in_executor(None, run_whisper)
    
    if not segments:
        raise RuntimeError("Whisper не нашел речь")

    # 3. Перевод Gemini батчами
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

    # 4. Запись SRT
    def _to_srt_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds - int(seconds)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            text = translated_segments.get(str(i), seg.text.strip())
            # Убираем лишние переносы, чтобы субтитры не ломали кадр
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
    
    loop = asyncio.get_running_loop()
    proc = await loop.run_in_executor(None, lambda: subprocess.run(cmd, capture_output=True, timeout=900))
    
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
    loop = asyncio.get_running_loop()
    proc = await loop.run_in_executor(None, lambda: subprocess.run(cmd, capture_output=True, timeout=300))
    
    if output_path.exists() and output_path.stat().st_size > 1000:
        return output_path
    
    logger.warning(f"[EngSubtitles] Ошибка склейки субтитров (default flag). Пробуем без default. stderr: {proc.stderr[-300:] if proc.stderr else ''}")
    
    # Резервный fallback - без disposition и без mov_text если контейнер конфликтует
    cmd_fallback = [
        ffmpeg, "-i", str(video_path), "-i", str(srt_path),
        "-c", "copy", "-c:s", "mov_text", "-metadata:s:s:0", "language=rus",
        "-y", str(output_path)
    ]
    proc2 = await loop.run_in_executor(None, lambda: subprocess.run(cmd_fallback, capture_output=True, timeout=300))
    if output_path.exists() and output_path.stat().st_size > 1000:
        return output_path
        
    logger.error(f"[EngSubtitles] Полный отказ склейки. stderr: {proc2.stderr[-300:] if proc2.stderr else ''}")
    return video_path
