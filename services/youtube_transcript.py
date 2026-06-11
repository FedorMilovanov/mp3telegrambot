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


def _clean_caption_text(line: str) -> str:
    line = html.unescape(str(line or ""))
    line = _TAG_RE.sub(" ", line)
    line = line.replace("♪", " ")
    line = _DUP_SPACE_RE.sub(" ", line).strip()
    return line


def vtt_to_timed_text(raw: str, *, max_chars: int = 120_000) -> str:
    """Parse WebVTT/SRV-like captions into compact [M:SS] text lines.

    Keeps timestamps but dedupes repeated auto-caption fragments. This is not a
    subtitle renderer; it is context for Gemini's Synopsis prompt.
    """
    if not raw:
        return ""
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    current_ts: int | None = None
    buf: list[str] = []
    seen_recent: list[str] = []

    def flush() -> None:
        nonlocal buf, current_ts
        if current_ts is None or not buf:
            buf = []
            return
        text = _clean_caption_text(" ".join(buf))
        buf = []
        if not text:
            return
        norm = re.sub(r"\W+", "", text.lower())
        if not norm or norm in seen_recent:
            return
        seen_recent.append(norm)
        if len(seen_recent) > 12:
            del seen_recent[:-12]
        out.append(f"[{_fmt_mmss(current_ts)}] {text}")

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line == "WEBVTT" or line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        tm = _VTT_TIME_RE.search(line)
        if tm:
            flush()
            current_ts = _sec_from_match(tm)
            # Some srv/vtt generators put text after timing settings on same cue;
            # normal WebVTT doesn't. We ignore timing metadata safely.
            continue
        if current_ts is None:
            continue
        if re.fullmatch(r"\d+", line):
            continue
        clean = _clean_caption_text(line)
        if clean:
            buf.append(clean)
        if sum(len(x) for x in out) > max_chars:
            break
    flush()
    text = "\n".join(out)
    return text[:max_chars].rstrip()


async def download_youtube_transcript_text(
    video_url: str,
    workdir: Path,
    *,
    lang: str = "en",
    max_chars: int = 120_000,
) -> str:
    """Download YouTube manual/auto captions via yt-dlp and return timed text.

    Returns empty string on any failure. Never raises into the main pipeline.
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
        stem = workdir / "yt_transcript"
        lang_root = (lang or "en").split("-", 1)[0].lower()
        sub_langs = os.getenv("SYNOPSIS_YT_TRANSCRIPT_LANGS", "").strip()
        if not sub_langs:
            sub_langs = f"{lang_root}.*,en.*,en"
        cmd = [
            *YTDLP_BASE_ARGS,
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs", sub_langs,
            "--sub-format", "vtt/best",
            "--output", str(stem) + ".%(ext)s",
            video_url,
        ]
        logger.info("[SynopsisTranscript] yt-dlp subtitles: langs=%s", sub_langs)

        def _run():
            kwargs = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            return subprocess.run(cmd, timeout=240, **kwargs)

        proc = await asyncio.get_running_loop().run_in_executor(None, _run)
        candidates = sorted(workdir.glob("yt_transcript*.vtt"), key=lambda p: p.stat().st_size, reverse=True)
        if not candidates:
            logger.info("[SynopsisTranscript] subtitles unavailable rc=%s: %s", proc.returncode, (proc.stderr or "")[-240:])
            return ""
        raw = candidates[0].read_text(encoding="utf-8", errors="replace")
        timed = vtt_to_timed_text(raw, max_chars=max_chars)
        if timed:
            logger.info(
                "[SynopsisTranscript] transcript ready: %s lines, %s chars (%s)",
                timed.count("\n") + 1, len(timed), candidates[0].name,
            )
        return timed
    except Exception as e:
        logger.info("[SynopsisTranscript] skip: %s", str(e)[:180])
        return ""
