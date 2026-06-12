#!/usr/bin/env python3
"""YouTube transcript helpers for dense Synopsis generation.

Goal: for long ENG Full materials, Gemini audio-only synopsis can collapse into
an article-like summary. If YouTube captions/auto-captions are available, we feed
a timed English transcript into Synopsis prompt so the model can produce a much
more verbatim structured Russian transcript.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_VTT_TIME_RE = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})(?:[.,](?P<ms>\d{1,3}))?\s*-->"
)
_TAG_RE = re.compile(r"<[^>]+>")
_DUP_SPACE_RE = re.compile(r"\s+")


def _env_enabled(name: str, default: bool = True) -> bool:
    raw = os.getenv(name, "")
    if not raw:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _sec_from_match(m: re.Match) -> int:
    return int(m.group("h")) * 3600 + int(m.group("m")) * 60 + int(m.group("s"))


def _fmt_mmss(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    mm, ss = divmod(rem, 60)
    if h:
        return f"{h}:{mm:02d}:{ss:02d}"
    return f"{mm}:{ss:02d}"


def timed_text_last_second(timed_text: str) -> int:
    """Last [M:SS]/[H:MM:SS] timestamp in cleaned transcript."""
    last = 0
    for m in re.finditer(r"\[(?:(\d+):)?(\d{1,2}):(\d{2})\]", str(timed_text or "")):
        h = int(m.group(1) or 0)
        last = max(last, h * 3600 + int(m.group(2)) * 60 + int(m.group(3)))
    return last


def _clean_caption_text(line: str) -> str:
    line = html.unescape(str(line or ""))
    line = _TAG_RE.sub(" ", line)
    line = line.replace("♪", " ")
    line = _DUP_SPACE_RE.sub(" ", line).strip()
    return line


def _word_key(word: str) -> str:
    return re.sub(r"[^\w']+", "", word.lower(), flags=re.UNICODE)


def _append_caption_delta(
    chunk_words: list[str],
    cue_text: str,
    *,
    context_words: list[str] | None = None,
) -> None:
    """Append only the non-repeated suffix of a rolling auto-caption cue."""
    cue_words = [w for w in cue_text.split() if _word_key(w)]
    if not cue_words:
        return
    base_words = chunk_words if chunk_words else (context_words or [])
    base_keys = [_word_key(w) for w in base_words]
    cue_keys = [_word_key(w) for w in cue_words]
    joined_base = " ".join(base_keys)
    joined_cue = " ".join(cue_keys)
    if joined_cue and joined_cue in joined_base:
        return
    max_olap = min(len(base_keys), len(cue_keys), 24)
    overlap = 0
    for n in range(max_olap, 0, -1):
        if base_keys[-n:] == cue_keys[:n]:
            overlap = n
            break
    chunk_words.extend(cue_words[overlap:])


def vtt_to_timed_text(raw: str, *, max_chars: int = 120_000, chunk_seconds: int = 25) -> str:
    """Parse WebVTT/SRV-like captions into compact [M:SS] text lines.

    YouTube auto-captions are often *rolling captions*: each cue repeats part of
    the previous cue. A naive cue-per-line dump becomes unreadable and wastes
    prompt budget. We merge cues into ~25s chunks and append only the new suffix.
    """
    if not raw:
        return ""
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[tuple[int, str]] = []
    current_ts: int | None = None
    buf: list[str] = []

    def flush_cue() -> None:
        nonlocal buf, current_ts
        if current_ts is None or not buf:
            buf = []
            return
        text = _clean_caption_text(" ".join(buf))
        buf = []
        if text:
            cues.append((current_ts, text))

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line == "WEBVTT" or line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        tm = _VTT_TIME_RE.search(line)
        if tm:
            flush_cue()
            current_ts = _sec_from_match(tm)
            continue
        if current_ts is None or re.fullmatch(r"\d+", line):
            continue
        clean = _clean_caption_text(line)
        if clean:
            buf.append(clean)
    flush_cue()

    out: list[str] = []
    chunk_start: int | None = None
    chunk_words: list[str] = []
    carry_words: list[str] = []

    def flush_chunk() -> None:
        nonlocal chunk_start, chunk_words, carry_words
        if chunk_start is None or not chunk_words:
            chunk_words = []
            return
        text = _DUP_SPACE_RE.sub(" ", " ".join(chunk_words)).strip()
        if text:
            out.append(f"[{_fmt_mmss(chunk_start)}] {text}")
        carry_words = chunk_words[-24:]
        chunk_words = []
        chunk_start = None

    for ts, cue_text in cues:
        if chunk_start is None:
            chunk_start = ts
        if chunk_words and ts - chunk_start >= chunk_seconds:
            flush_chunk()
            chunk_start = ts
        _append_caption_delta(chunk_words, cue_text, context_words=carry_words)
        if sum(len(x) for x in out) + len(chunk_words) * 7 > max_chars:
            break
    flush_chunk()
    text = "\n".join(out)
    if len(text) > max_chars:
        clipped = text[:max_chars]
        if "\n" in clipped:
            clipped = clipped.rsplit("\n", 1)[0]
        return clipped.rstrip()
    return text.rstrip()


async def download_youtube_transcript_text(
    video_url: str,
    workdir: Path,
    *,
    lang: str = "en",
    max_chars: int = 120_000,
    expected_duration: float = 0.0,
) -> str:
    """Download YouTube manual/auto captions via yt-dlp and return timed text.

    Manual captions are attempted first. Auto captions are a fallback because
    they often contain rolling duplicates and ASR mistakes. Returns empty string
    on any failure; never raises into the main pipeline.
    """
    if not _env_enabled("SYNOPSIS_YT_TRANSCRIPT", True):
        return ""
    yt_dlp = shutil.which("yt-dlp")
    if not yt_dlp:
        # YTDLP_BASE_ARGS normally invokes module via python -m yt_dlp, so this
        # branch is not fatal; import below still supplies the command.
        pass
    try:
        from services.ffmpeg import YTDLP_BASE_ARGS
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        for old in workdir.glob("yt_transcript*.vtt"):
            try:
                old.unlink()
            except OSError:
                pass
        stem = workdir / "yt_transcript_%(id)s"
        lang_root = (lang or "en").split("-", 1)[0].lower()
        sub_langs = os.getenv("SYNOPSIS_YT_TRANSCRIPT_LANGS", "").strip()
        if not sub_langs:
            # yt-dlp accepts comma-separated globs. Keep order but avoid noisy
            # duplicates like en.*,en.*,en in logs/requests.
            prefs = [f"{lang_root}.*", lang_root, "en.*", "en"]
            seen: set[str] = set()
            sub_langs = ",".join(x for x in prefs if not (x in seen or seen.add(x)))

        def _cmd(*, auto: bool) -> list[str]:
            return [
                *YTDLP_BASE_ARGS,
                "--skip-download",
                "--write-auto-subs" if auto else "--write-subs",
                "--sub-langs", sub_langs,
                "--sub-format", "vtt/best",
                "--output", str(stem) + ".%(ext)s",
                video_url,
            ]

        def _run(cmd: list[str]):
            kwargs = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            return subprocess.run(cmd, timeout=240, **kwargs)

        loop = asyncio.get_running_loop()
        last_proc = None
        candidates: list[Path] = []
        source_kind = "manual"
        for auto in (False, True):
            for old in workdir.glob("yt_transcript*.vtt"):
                try:
                    old.unlink()
                except OSError:
                    pass
            source_kind = "auto" if auto else "manual"
            logger.info("[SynopsisTranscript] yt-dlp %s subtitles: langs=%s", source_kind, sub_langs)
            last_proc = await loop.run_in_executor(None, lambda a=auto: _run(_cmd(auto=a)))
            candidates = sorted(workdir.glob("yt_transcript*.vtt"), key=lambda p: p.stat().st_size, reverse=True)
            if candidates:
                break

        if not candidates:
            logger.info(
                "[SynopsisTranscript] subtitles unavailable rc=%s: %s",
                getattr(last_proc, "returncode", "?"), (getattr(last_proc, "stderr", "") or "")[-240:],
            )
            return ""
        raw = candidates[0].read_text(encoding="utf-8", errors="replace")
        timed = vtt_to_timed_text(raw, max_chars=max_chars)
        if timed and expected_duration and expected_duration >= 600:
            try:
                min_cov = float(os.getenv("SYNOPSIS_YT_TRANSCRIPT_MIN_COVERAGE", "0.70") or "0.70")
            except ValueError:
                min_cov = 0.70
            last_ts = timed_text_last_second(timed)
            coverage = last_ts / max(1.0, float(expected_duration))
            if coverage < max(0.1, min(min_cov, 0.98)):
                logger.info(
                    "[SynopsisTranscript] rejected: coverage %.0f%% < %.0f%% (last=%ss duration=%ss)",
                    coverage * 100, min_cov * 100, last_ts, int(expected_duration),
                )
                return ""
        if timed:
            logger.info(
                "[SynopsisTranscript] transcript ready: %s lines, %s chars (%s, %s)",
                timed.count("\n") + 1, len(timed), candidates[0].name, source_kind,
            )
        return timed
    except Exception as e:
        logger.info("[SynopsisTranscript] skip: %s", str(e)[:180])
        return ""
