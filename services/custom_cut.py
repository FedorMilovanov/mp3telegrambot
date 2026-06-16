#!/usr/bin/env python3
"""
Custom Cut — вырезка фрагмента видео по текстовым фразам.

/cut video_id "от фразы" "до фразы" [shorts|wide]

Flow:
1. Whisper транскрибирует видео с word-level timestamps
2. Fuzzy-поиск начальной и конечной фразы в транскрипции
3. ffmpeg вырезает фрагмент
4. Опционально: вертикальный crop (shorts) + субтитры
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from core.globals import DOWNLOAD_DIR

logger = logging.getLogger(__name__)


def _normalize_for_search(text: str) -> str:
    """Normalize text for fuzzy phrase matching."""
    t = text.lower().strip()
    t = t.replace("ё", "е")
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def find_phrase_timestamp(
    words: list[dict],
    phrase: str,
    *,
    from_seconds: float = 0.0,
    position: str = "start",  # "start" → return start of match, "end" → return end
) -> Optional[float]:
    """Find a phrase in word-level Whisper output and return its timestamp.
    
    Strategy: first try exact substring match across the full word sequence,
    then fall back to fuzzy (best overlap) match.
    
    Args:
        words: list of {"word": str, "start": float, "end": float}
        phrase: target phrase to find
        from_seconds: only search after this timestamp
        position: "start" returns start of first matched word,
                  "end" returns end of last matched word
    
    Returns:
        timestamp in seconds, or None if not found
    """
    if not words or not phrase:
        return None
    
    target = _normalize_for_search(phrase)
    target_words = target.split()
    if not target_words:
        return None
    
    # Filter words after from_seconds
    filtered = [(i, w) for i, w in enumerate(words) if w.get("start", 0) >= from_seconds]
    if not filtered:
        filtered = list(enumerate(words))
    
    window_size = len(target_words)
    
    # === PASS 1: Exact substring match ===
    # Use a tight sliding window (exactly target_words length, no tolerance)
    # to avoid matching across phrase boundaries
    for start_pos in range(len(filtered)):
        window_end = min(start_pos + window_size, len(filtered))
        window_texts = []
        for j in range(start_pos, window_end):
            _, wj = filtered[j]
            word_norm = _normalize_for_search(wj.get("word", ""))
            if word_norm:
                window_texts.append(word_norm)
        window_joined = " ".join(window_texts)
        if target in window_joined:
            if position == "start":
                _, match_w = filtered[start_pos]
                return match_w.get("start", 0.0)
            else:
                _, match_w = filtered[min(window_end - 1, len(filtered) - 1)]
                return match_w.get("end", 0.0)
    
    # === PASS 2: Fuzzy match (best overlap score) ===
    best_score = 0.0
    best_start = None
    best_end = None
    
    for start_pos in range(len(filtered)):
        window_end = min(start_pos + window_size + 2, len(filtered))
        window_words = []
        for j in range(start_pos, window_end):
            _, w = filtered[j]
            window_words.append(_normalize_for_search(w.get("word", "")))
        
        window_text = " ".join(window_words)
        
        matches = sum(1 for tw in target_words if tw in window_text)
        score = matches / len(target_words)
        
        if score > best_score and score >= 0.6:
            best_score = score
            best_start = start_pos
            best_end = min(start_pos + window_size - 1, len(filtered) - 1)
    
    if best_start is not None:
        if position == "start":
            _, w = filtered[best_start]
            return w.get("start", 0.0)
        else:
            _, w = filtered[best_end]
            return w.get("end", 0.0)
    
    return None


async def transcribe_full_video(
    video_path: Path,
    language: str = "ru",
    video_url: str = "",
) -> list[dict]:
    """Get word-level timestamps for phrase search.
    
    Strategy:
    1. Try YouTube captions first (instant, no CPU cost)
    2. Fall back to Whisper with medium model (slow but works offline)
    
    Returns flat list of {"word": str, "start": float, "end": float}.
    """
    # === FAST PATH: YouTube transcript ===
    if video_url:
        try:
            from services.youtube_transcript import download_youtube_transcript_text
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                timed = await download_youtube_transcript_text(
                    video_url, Path(tmpdir),
                    lang=language, max_chars=500_000,
                )
                if timed:
                    # Parse timed text "[M:SS] word word word\n" into word list
                    words = []
                    for line in timed.split("\n"):
                        line = line.strip()
                        if not line:
                            continue
                        m = re.match(r'\[(\d+):(\d{2})\]\s*(.*)', line)
                        if not m:
                            continue
                        ts = int(m.group(1)) * 60 + int(m.group(2))
                        text = m.group(3).strip()
                        if not text:
                            continue
                        line_words = text.split()
                        # Distribute words evenly across the ~25s chunk
                        chunk_duration = 25.0  # default chunk_seconds
                        word_duration = chunk_duration / max(len(line_words), 1)
                        for j, w in enumerate(line_words):
                            words.append({
                                "word": w,
                                "start": round(ts + j * word_duration, 2),
                                "end": round(ts + (j + 1) * word_duration, 2),
                            })
                    if words:
                        logger.info("[CustomCut] YouTube transcript: %d words (instant)", len(words))
                        return words
        except Exception as e:
            logger.info("[CustomCut] YouTube transcript unavailable: %s", str(e)[:80])
    
    # === SLOW PATH: Whisper ===
    from services.shorts_video import _get_whisper_model, HAS_FASTER_WHISPER
    
    if not HAS_FASTER_WHISPER:
        logger.warning("[CustomCut] faster-whisper not installed")
        return []
    
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning("[CustomCut] ffmpeg not found")
        return []
    
    # Extract audio to WAV
    wav_path = Path(tempfile.mktemp(suffix=".wav"))
    try:
        cmd = [ffmpeg, "-i", str(video_path), "-ar", "16000", "-ac", "1", "-f", "wav", "-y", str(wav_path)]
        loop = asyncio.get_running_loop()
        proc = await loop.run_in_executor(
            None, lambda: subprocess.run(cmd, capture_output=True, timeout=600)
        )
        if proc.returncode != 0 or not wav_path.exists():
            logger.warning("[CustomCut] Audio extraction failed")
            return []
        
        logger.info("[CustomCut] Transcribing full video (%s)...", video_path.name)
        
        def _run():
            # Use medium model for speed: full video transcription with large-v3
            # takes 10-20 min on CPU. Medium is ~3x faster with acceptable accuracy
            # for phrase-finding (we need positions, not perfect text).
            _cut_model = os.getenv("CUSTOM_CUT_WHISPER_MODEL", "medium").strip() or "medium"
            model = _get_whisper_model(_cut_model)
            segs, info = model.transcribe(
                str(wav_path),
                language=language,
                beam_size=1,  # Fast mode for full video
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                word_timestamps=True,
            )
            all_words = []
            for seg in segs:
                for w in (seg.words or []):
                    all_words.append({
                        "word": w.word.strip(),
                        "start": round(w.start, 2),
                        "end": round(w.end, 2),
                    })
            return all_words
        
        words = await loop.run_in_executor(None, _run)
        logger.info("[CustomCut] Transcribed %d words", len(words))
        return words
        
    except Exception as e:
        logger.warning("[CustomCut] Transcription failed: %s", e)
        return []
    finally:
        wav_path.unlink(missing_ok=True)


async def custom_cut_video(
    video_path: Path,
    start_phrase: str,
    end_phrase: str,
    output_path: Path,
    mode: str = "wide",  # "wide" (16:9) or "shorts" (9:16)
    language: str = "ru",
    burn_subtitles: bool = True,
    video_url: str = "",
) -> Optional[Path]:
    """Cut a video segment between two phrases.
    
    Args:
        video_path: source video
        start_phrase: phrase to find for start point
        end_phrase: phrase to find for end point
        output_path: where to save the cut
        mode: "wide" keeps original aspect, "shorts" crops to 9:16
        language: language for Whisper
        burn_subtitles: if True, burn subtitles into the clip
        video_url: YouTube URL for fast transcript lookup
    
    Returns:
        Path to output file, or None on failure
    """
    # 1. Transcribe (fast: YouTube captions, slow: Whisper fallback)
    words = await transcribe_full_video(video_path, language=language, video_url=video_url)
    if not words:
        return None
    
    # 2. Find phrases
    start_time = find_phrase_timestamp(words, start_phrase, position="start")
    if start_time is None:
        logger.warning("[CustomCut] Start phrase not found: '%s'", start_phrase)
        return None
    
    end_time = find_phrase_timestamp(words, end_phrase, from_seconds=start_time + 1, position="end")
    if end_time is None:
        logger.warning("[CustomCut] End phrase not found: '%s'", end_phrase)
        return None
    
    if end_time <= start_time:
        logger.warning("[CustomCut] End (%s) <= start (%s)", end_time, start_time)
        return None
    
    duration = end_time - start_time
    if duration > 600:
        logger.warning("[CustomCut] Segment too long: %.0fs (max 600s)", duration)
        return None
    if duration < 2:
        logger.warning("[CustomCut] Segment too short: %.1fs", duration)
        return None
    
    logger.info(
        "[CustomCut] Found: '%.30s' at %.1fs → '%.30s' at %.1fs (%.0fs)",
        start_phrase, start_time, end_phrase, end_time, duration,
    )
    
    # 3. Cut + optional reformat
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    
    # Add small padding (0.3s before, 0.5s after)
    padded_start = max(0, start_time - 0.3)
    padded_end = end_time + 0.5
    
    from services.ffmpeg import _get_video_encoder
    _enc, _quality, _preset = _get_video_encoder()
    
    vf_filters = []
    if mode == "shorts":
        # Crop to 9:16 (center crop)
        vf_filters.append("crop=ih*9/16:ih")
    
    cmd = [
        ffmpeg,
        "-ss", f"{padded_start:.2f}",
        "-i", str(video_path),
        "-t", f"{padded_end - padded_start:.2f}",
    ]
    
    if vf_filters:
        cmd += ["-vf", ",".join(vf_filters)]
    
    cmd += [
        "-c:v", _enc, *_quality, *_preset,
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-y", str(output_path),
    ]
    
    loop = asyncio.get_running_loop()
    proc = await loop.run_in_executor(
        None, lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    )
    
    if proc.returncode != 0 or not output_path.exists():
        logger.warning("[CustomCut] ffmpeg failed: %s", (proc.stderr or "")[-300:])
        return None
    
    # 4. Optional: burn subtitles from the cut segment
    if burn_subtitles:
        try:
            from services.shorts_video import transcribe_short_clip, burn_subtitles_into_short
            
            sub_segments = await transcribe_short_clip(output_path)
            if sub_segments:
                sub_output = output_path.with_suffix(".sub.mp4")
                ok = await burn_subtitles_into_short(output_path, sub_output, sub_segments)
                if ok and sub_output.exists() and sub_output.stat().st_size > 10240:
                    output_path.unlink(missing_ok=True)
                    sub_output.rename(output_path)
                    logger.info("[CustomCut] Subtitles burned in")
                else:
                    sub_output.unlink(missing_ok=True)
        except Exception as e:
            logger.info("[CustomCut] Subtitle burn skipped: %s", str(e)[:80])
    
    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info("[CustomCut] Done: %s (%.1f MB, %.0fs)", output_path.name, size_mb, duration)
    return output_path
