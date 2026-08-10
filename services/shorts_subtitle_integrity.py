#!/usr/bin/env python3
"""Deterministic ASS generation and validation for Shorts subtitles.

This module intentionally does not stretch Whisper word timings across real
silence.  A pause is a semantic/render boundary, not missing data to be filled.
The generated ASS is validated before FFmpeg sees it so malformed, overlapping
or abnormally long karaoke events fail closed instead of being published.
"""
from __future__ import annotations

import math
import re
from typing import Any

COLOUR_ACTIVE = "&H0000E5FF"
COLOUR_INACTIVE = "&H00FFFFFF"
MIN_WORD_DURATION = 0.08
MAX_WORD_DURATION = 2.50
MAX_KARAOKE_HOLD = 3.00
MAX_PAUSE_IN_CHUNK = 0.35
MAX_CHARS = 38

_PURE_PUNCT_RE = re.compile(r"^[^\w]+$", re.UNICODE)
_PARTICLES = {"-то", "-либо", "-нибудь", "-ка", "-таки", "-де", "-с", "-ж", "-же"}
_SENTENCE_END = (".", "!", "?")


def _finite(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _ass_time(seconds: float) -> str:
    value = max(0.0, _finite(seconds, 0.0))
    centiseconds = max(0, int(round(value * 100.0)))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _parse_ass_time(value: str) -> float:
    parts = str(value or "").strip().split(":")
    if len(parts) != 3:
        raise ValueError(f"invalid ASS time {value!r}")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError(f"invalid ASS time {value!r}")
    return hours * 3600.0 + minutes * 60.0 + seconds


def _escape_ass_text(text: str) -> str:
    return (
        str(text or "")
        .replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .strip()
    )


def _collect_words(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for segment in segments or []:
        seg_start = max(0.0, _finite(segment.get("start"), 0.0))
        seg_end = max(seg_start + MIN_WORD_DURATION, _finite(segment.get("end"), seg_start + 1.0))
        raw_words = segment.get("words") or []
        if raw_words:
            for item in raw_words:
                text = str(item.get("word") or "").strip()
                if not text:
                    continue
                words.append(
                    {
                        "word": text,
                        "start": _finite(item.get("start"), seg_start),
                        "end": _finite(item.get("end"), seg_end),
                    }
                )
            continue

        text_words = str(segment.get("text") or "").strip().split()
        if not text_words:
            continue
        width = max(MIN_WORD_DURATION, (seg_end - seg_start) / len(text_words))
        for index, text in enumerate(text_words):
            start = seg_start + index * width
            words.append(
                {
                    "word": text,
                    "start": start,
                    "end": min(seg_end, start + max(MIN_WORD_DURATION, width * 0.9)),
                }
            )
    return words


def _normalize_words(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Make word intervals monotonic without inventing speech across silence."""
    out: list[dict[str, Any]] = []
    for raw in words:
        text = str(raw.get("word") or "").strip()
        if not text:
            continue
        start = max(0.0, _finite(raw.get("start"), 0.0))
        end = _finite(raw.get("end"), start + MIN_WORD_DURATION)
        if out and start < out[-1]["end"]:
            start = out[-1]["end"]
        end = max(end, start + MIN_WORD_DURATION)
        end = min(end, start + MAX_WORD_DURATION)
        out.append({"word": text, "start": start, "end": end})
    return out


def _merge_tokens(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge Russian hyphen particles and punctuation-only Whisper tokens."""
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(words):
        current = dict(words[index])
        text = str(current.get("word") or "").strip()

        if text.lower() in _PARTICLES and result:
            previous = result[-1]
            previous["word"] = str(previous.get("word") or "").strip() + text
            previous["end"] = max(float(previous["end"]), float(current["end"]))
            index += 1
            continue

        if text.endswith("-") and index + 1 < len(words):
            following = words[index + 1]
            current["word"] = text + str(following.get("word") or "").strip()
            current["end"] = max(float(current["end"]), float(following["end"]))
            result.append(current)
            index += 2
            continue

        if text and result and _PURE_PUNCT_RE.match(text):
            previous = result[-1]
            previous["word"] = str(previous.get("word") or "").strip() + text
            previous["end"] = max(float(previous["end"]), float(current["end"]))
            index += 1
            continue

        result.append(current)
        index += 1
    return result


def _chunk_words(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split on real pauses first, then punctuation and visual width.

    Hard pause boundaries are never suppressed by a preceding preposition.  The
    old implementation could cancel a pause cut after words such as ``в`` or
    ``на`` and keep one karaoke event alive through several seconds of silence.
    """
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_len = 0

    for word in words:
        text = str(word.get("word") or "").strip()
        if not text:
            continue
        projected = current_len + len(text) + (1 if current else 0)
        hard_pause = False
        sentence_break = False
        clause_break = False
        if current:
            previous = current[-1]
            pause = max(0.0, float(word["start"]) - float(previous["end"]))
            previous_text = str(previous.get("word") or "").strip()
            hard_pause = pause > MAX_PAUSE_IN_CHUNK
            sentence_break = previous_text.endswith(_SENTENCE_END)
            clause_break = previous_text.endswith(",") and current_len >= MAX_CHARS // 2

        if current and (hard_pause or sentence_break or clause_break or projected > MAX_CHARS):
            chunks.append(current)
            current = []
            current_len = 0

        current.append(word)
        current_len += len(text) + (1 if len(current) > 1 else 0)

    if current:
        chunks.append(current)
    return chunks


def _wrap_chunk(words: list[dict[str, Any]], max_line_chars: int = MAX_CHARS) -> str:
    text_words = [str(word.get("word") or "").strip() for word in words]
    text_words = [word for word in text_words if word]
    text = " ".join(text_words)
    if len(text) <= max_line_chars:
        return text

    midpoint = len(text) / 2.0
    best_index = 1
    best_distance = float("inf")
    cursor = 0
    for index, word in enumerate(text_words[:-1], 1):
        cursor += len(word) + (1 if index > 1 else 0)
        distance = abs(cursor - midpoint)
        if distance < best_distance:
            best_index = index
            best_distance = distance
    return " ".join(text_words[:best_index]) + r"\N" + " ".join(text_words[best_index:])


def _header(font_name: str) -> str:
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 720\n"
        "PlayResY: 1280\n"
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


def validate_ass_document(
    document: str,
    *,
    karaoke: bool,
    max_karaoke_hold: float = MAX_KARAOKE_HOLD,
) -> tuple[str, ...]:
    """Return structural/timing problems in a generated ASS document."""
    issues: list[str] = []
    previous_end = -1.0
    dialogue_count = 0
    for line_no, line in enumerate(str(document or "").splitlines(), 1):
        if not line.startswith("Dialogue:"):
            continue
        dialogue_count += 1
        fields = line.split(",", 9)
        if len(fields) != 10:
            issues.append(f"line {line_no}: malformed Dialogue")
            continue
        try:
            start = _parse_ass_time(fields[1])
            end = _parse_ass_time(fields[2])
        except (TypeError, ValueError) as exc:
            issues.append(f"line {line_no}: {exc}")
            continue
        if end <= start:
            issues.append(f"line {line_no}: non-positive interval {start:.2f}-{end:.2f}")
        if previous_end >= 0 and start < previous_end - 0.011:
            issues.append(f"line {line_no}: overlaps previous event")
        if karaoke and end - start > max_karaoke_hold + 0.011:
            issues.append(f"line {line_no}: karaoke hold {end - start:.2f}s is too long")
        if not fields[9].strip():
            issues.append(f"line {line_no}: empty subtitle text")
        previous_end = max(previous_end, end)
    if dialogue_count == 0:
        issues.append("ASS contains no Dialogue events")
    return tuple(issues)


def generate_ass_from_segments(
    segments: list[dict[str, Any]],
    *,
    karaoke: bool = True,
) -> str:
    """Build a validated ASS document from Whisper segments."""
    from services.shorts_video_impl import _pick_subtitle_font

    words = _merge_tokens(_normalize_words(_collect_words(segments)))
    if not words:
        raise ValueError("subtitle transcript contains no timed words")
    chunks = _chunk_words(words)
    lines = [_header(_pick_subtitle_font())]

    if not karaoke:
        for chunk in chunks:
            start = float(chunk[0]["start"])
            end = max(float(chunk[-1]["end"]), start + MIN_WORD_DURATION)
            text = _escape_ass_text(_wrap_chunk(chunk))
            lines.append(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,"
                rf"{{\fad(80,60)}}{text}"
            )
    else:
        for chunk in chunks:
            clean = [_escape_ass_text(str(word.get("word") or "")) for word in chunk]
            for index, word in enumerate(chunk):
                start = float(word["start"])
                natural_end = max(float(word["end"]), start + MIN_WORD_DURATION)
                if index + 1 < len(chunk):
                    next_start = float(chunk[index + 1]["start"])
                    gap = max(0.0, next_start - natural_end)
                    end = next_start if gap <= MAX_PAUSE_IN_CHUNK else natural_end
                else:
                    end = natural_end
                end = min(max(end, start + MIN_WORD_DURATION), start + MAX_KARAOKE_HOLD)

                rendered: list[str] = []
                for word_index, text in enumerate(clean):
                    colour = COLOUR_ACTIVE if word_index == index else COLOUR_INACTIVE
                    rendered.append(f"{{\\c{colour}}}{text}{{\\c{COLOUR_INACTIVE}}}")
                lines.append(
                    f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,"
                    + " ".join(rendered)
                )

    document = "\n".join(lines)
    issues = validate_ass_document(document, karaoke=karaoke)
    if issues:
        raise ValueError("invalid generated ASS: " + "; ".join(issues[:8]))
    return document


__all__ = [
    "MAX_KARAOKE_HOLD",
    "MAX_PAUSE_IN_CHUNK",
    "generate_ass_from_segments",
    "validate_ass_document",
]
