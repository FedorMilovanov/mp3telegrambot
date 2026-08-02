#!/usr/bin/env python3
"""Apply the final source-backed chat findings closure, then remove itself."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


def _replace_between(
    text: str,
    start_marker: str,
    end_marker: str,
    replacement: str,
    *,
    label: str,
) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f"{label}: start marker missing")
    end = text.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f"{label}: end marker missing")
    return text[:start] + replacement + text[end:]


MEDIA_PROBE = r'''#!/usr/bin/env python3
"""Final media timing, size and silence evidence for public video delivery."""
from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MediaProbe:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    audio_sample_rate: int = 0
    has_video: bool = False
    has_audio: bool = False
    size_mb: float = 0.0


@dataclass(frozen=True)
class DeliveryTiming:
    source_start: float
    source_end: float
    raw_duration: float
    delivery_duration: float
    speed_applied: bool


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _positive_int(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, result)


def file_size_mb(path: Path) -> float:
    try:
        return path.stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


def _probe_from_payload(path: Path, payload: dict[str, Any]) -> MediaProbe:
    streams = payload.get("streams") if isinstance(payload, dict) else []
    streams = streams if isinstance(streams, list) else []
    video = next(
        (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"),
        {},
    )
    audio = next(
        (item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"),
        {},
    )
    format_payload = payload.get("format") if isinstance(payload, dict) else {}
    format_payload = format_payload if isinstance(format_payload, dict) else {}
    duration = _finite_float(format_payload.get("duration"))
    if duration <= 0:
        duration = max(
            (_finite_float(item.get("duration")) for item in streams if isinstance(item, dict)),
            default=0.0,
        )
    return MediaProbe(
        duration=max(0.0, duration),
        width=_positive_int(video.get("width")),
        height=_positive_int(video.get("height")),
        audio_sample_rate=_positive_int(audio.get("sample_rate")),
        has_video=bool(video),
        has_audio=bool(audio),
        size_mb=file_size_mb(path),
    )


def probe_media(path: Path, *, timeout: int = 20) -> MediaProbe | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not path.exists():
        return None
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,duration,width,height,sample_rate",
        "-of",
        "json",
        str(path),
    ]
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if process.returncode != 0:
            return None
        payload = json.loads(process.stdout or "{}")
        if not isinstance(payload, dict):
            return None
        return _probe_from_payload(path, payload)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


async def probe_media_async(path: Path, *, timeout: int = 20) -> MediaProbe | None:
    return await asyncio.to_thread(probe_media, path, timeout=timeout)


def resolve_delivery_timing(
    *,
    source_start: float,
    raw_duration: float,
    source_duration: float = 0.0,
    speed: float = 1.0,
    speed_applied: bool = False,
    final_duration: float = 0.0,
) -> DeliveryTiming:
    start = max(0.0, _finite_float(source_start))
    raw = max(0.001, _finite_float(raw_duration, 0.001))
    source_end = start + raw
    source_limit = _finite_float(source_duration)
    if source_limit > 0:
        source_end = min(source_limit, source_end)
    speed_value = max(0.01, _finite_float(speed, 1.0))
    final = _finite_float(final_duration)
    if final <= 0:
        final = raw / speed_value if speed_applied else raw
    return DeliveryTiming(
        source_start=round(start, 3),
        source_end=round(max(start, source_end), 3),
        raw_duration=round(raw, 3),
        delivery_duration=round(max(0.001, final), 3),
        speed_applied=bool(speed_applied),
    )


def parse_silencedetect(stderr: str, *, duration: float) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    pending: float | None = None
    for line in str(stderr or "").splitlines():
        if "silence_start:" in line:
            token = line.rsplit("silence_start:", 1)[1].strip().split()[0]
            value = _finite_float(token, -1.0)
            pending = value if value >= 0 else pending
            continue
        if "silence_end:" not in line:
            continue
        token = line.rsplit("silence_end:", 1)[1].strip().split()[0]
        end = _finite_float(token, -1.0)
        if pending is not None and end > pending:
            intervals.append((pending, end))
        pending = None
    if pending is not None and duration > pending:
        intervals.append((pending, duration))

    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1] + 0.08:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(round(start, 3), round(end, 3)) for start, end in merged]


def evaluate_highlights_delivery(
    probe: MediaProbe | None,
    silence_intervals: list[tuple[float, float]],
    *,
    expected_duration: float,
    max_internal_silence: float,
) -> dict[str, Any]:
    reasons: list[str] = []
    if probe is None:
        return {
            "policy": "final-render-highlights-delivery-v2",
            "accepted": False,
            "reasons": ["ffprobe_unavailable_or_invalid"],
        }

    if not probe.has_video:
        reasons.append("video_stream_missing")
    if not probe.has_audio:
        reasons.append("audio_stream_missing")
    if probe.width != 720 or probe.height != 1280:
        reasons.append("unexpected_dimensions")
    if probe.audio_sample_rate != 48000:
        reasons.append("unexpected_audio_sample_rate")

    expected = max(0.0, _finite_float(expected_duration))
    tolerance = max(0.75, expected * 0.015)
    duration_delta = abs(probe.duration - expected) if expected > 0 else 0.0
    if expected > 0 and duration_delta > tolerance:
        reasons.append("duration_mismatch")

    bad_silences = []
    for start, end in silence_intervals:
        silence_duration = max(0.0, end - start)
        touches_edge = start <= 0.35 or end >= probe.duration - 0.35
        if touches_edge and silence_duration <= 0.55:
            continue
        if silence_duration > max_internal_silence:
            bad_silences.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(silence_duration, 3),
                }
            )
    if bad_silences:
        reasons.append("long_internal_silence")

    return {
        "policy": "final-render-highlights-delivery-v2",
        "accepted": not reasons,
        "reasons": reasons,
        "probe": asdict(probe),
        "expected_duration": round(expected, 3),
        "duration_delta": round(duration_delta, 3),
        "duration_tolerance": round(tolerance, 3),
        "max_internal_silence": round(max_internal_silence, 3),
        "silence_intervals": [
            {"start": start, "end": end, "duration": round(end - start, 3)}
            for start, end in silence_intervals
        ],
        "bad_silences": bad_silences,
    }


async def verify_highlights_delivery(
    path: Path,
    *,
    expected_duration: float,
) -> dict[str, Any]:
    probe = await probe_media_async(path)
    if probe is None:
        return evaluate_highlights_delivery(
            None,
            [],
            expected_duration=expected_duration,
            max_internal_silence=2.8,
        )

    try:
        max_silence = float(os.getenv("HIGHLIGHTS_FINAL_MAX_SILENCE_SECONDS", "2.8"))
    except ValueError:
        max_silence = 2.8
    max_silence = min(6.0, max(1.2, max_silence))
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {
            "policy": "final-render-highlights-delivery-v2",
            "accepted": False,
            "reasons": ["ffmpeg_unavailable"],
            "probe": asdict(probe),
        }
    command = [
        ffmpeg,
        "-hide_banner",
        "-i",
        str(path),
        "-af",
        f"silencedetect=noise=-38dB:d={max_silence:.3f}",
        "-f",
        "null",
        "-",
    ]
    try:
        process = await asyncio.to_thread(
            subprocess.run,
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "policy": "final-render-highlights-delivery-v2",
            "accepted": False,
            "reasons": [f"silence_probe_error:{type(exc).__name__}"],
            "probe": asdict(probe),
        }
    intervals = parse_silencedetect(process.stderr, duration=probe.duration)
    return evaluate_highlights_delivery(
        probe,
        intervals,
        expected_duration=expected_duration,
        max_internal_silence=max_silence,
    )


__all__ = [
    "DeliveryTiming",
    "MediaProbe",
    "evaluate_highlights_delivery",
    "file_size_mb",
    "parse_silencedetect",
    "probe_media",
    "probe_media_async",
    "resolve_delivery_timing",
    "verify_highlights_delivery",
]
'''

GEMINI_STATUS = r'''#!/usr/bin/env python3
"""Current official Gemini model diagnostics for startup reporting.

Reviewed against the official Gemini model and deprecation pages on 2026-08-03.
The generation config itself remains adaptive in ``core.globals``.
"""
from __future__ import annotations

from dataclasses import dataclass

POLICY = "official-gemini-model-status-2026-08-03-v1"

_CURRENT_GA = {
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
}
_CURRENT_PREVIEW = {
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
}
_LEGACY_MIGRATION = {
    "gemini-2.5-flash": "gemini-3.6-flash",
    "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
    "gemini-2.5-pro": "gemini-3.1-pro-preview",
}
_SHUTDOWN = {
    "gemini-3-pro-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-pro-preview",
    "gemini-2.5-pro-preview-03-25",
    "gemini-2.5-pro-preview-05-06",
    "gemini-2.5-pro-preview-06-05",
    "gemini-2.5-flash-preview-05-20",
    "gemini-2.5-flash-preview-09-25",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-lite-001",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
    "gemini-1.5-flash-001",
    "gemini-1.0-pro",
    "gemini-pro",
    "gemini-pro-vision",
}


@dataclass(frozen=True)
class GeminiModelDiagnostic:
    level: str
    message: str
    policy: str = POLICY


def classify_gemini_model(model_name: str) -> GeminiModelDiagnostic:
    model = str(model_name or "").strip().lower()
    if model in _CURRENT_GA or model in {"gemini-flash-latest"}:
        return GeminiModelDiagnostic(
            "info",
            f"GEMINI_MODEL='{model}' — актуальная production-модель Gemini API.",
        )
    if model in _CURRENT_PREVIEW:
        replacement = "gemini-3.6-flash" if "flash" in model else "gemini-3.1-pro-preview"
        return GeminiModelDiagnostic(
            "info",
            f"GEMINI_MODEL='{model}' — действующая preview-модель; для стабильного Flash используйте {replacement}.",
        )
    if model in _LEGACY_MIGRATION:
        return GeminiModelDiagnostic(
            "warning",
            f"GEMINI_MODEL='{model}' поддерживается до 2026-10-16; запланируйте переход на {_LEGACY_MIGRATION[model]}.",
        )
    if model in _SHUTDOWN:
        return GeminiModelDiagnostic(
            "error",
            f"GEMINI_MODEL='{model}' отключена или снята с поддержки; используйте gemini-3.6-flash.",
        )
    if not model:
        return GeminiModelDiagnostic("error", "GEMINI_MODEL не задан.")
    return GeminiModelDiagnostic(
        "warning",
        f"GEMINI_MODEL='{model}' не входит в проверенный официальный каталог на 2026-08-03; проверьте models.list.",
    )


__all__ = ["GeminiModelDiagnostic", "POLICY", "classify_gemini_model"]
'''

TEST_MEDIA = r'''from services.media_delivery_probe import (
    MediaProbe,
    evaluate_highlights_delivery,
    parse_silencedetect,
    resolve_delivery_timing,
)


def test_failed_speed_does_not_shrink_delivery_metadata() -> None:
    timing = resolve_delivery_timing(
        source_start=338.5,
        raw_duration=129.0,
        source_duration=3596.0,
        speed=1.5,
        speed_applied=False,
        final_duration=0.0,
    )
    assert timing.source_end == 467.5
    assert timing.delivery_duration == 129.0
    assert timing.speed_applied is False


def test_successful_speed_uses_measured_final_duration() -> None:
    timing = resolve_delivery_timing(
        source_start=338.5,
        raw_duration=129.0,
        source_duration=3596.0,
        speed=1.5,
        speed_applied=True,
        final_duration=86.12,
    )
    assert timing.source_end == 467.5
    assert timing.delivery_duration == 86.12
    assert timing.speed_applied is True


def test_old_washer_highlights_signature_is_rejected() -> None:
    probe = MediaProbe(
        duration=78.1,
        width=720,
        height=1280,
        audio_sample_rate=96000,
        has_video=True,
        has_audio=True,
        size_mb=16.8,
    )
    stderr = """
    silence_start: 41.870979
    silence_end: 47.639667 | silence_duration: 5.768687
    silence_start: 56.400677
    silence_end: 59.297187 | silence_duration: 2.896510
    silence_start: 59.297719
    silence_end: 64.557312 | silence_duration: 5.259594
    """
    intervals = parse_silencedetect(stderr, duration=probe.duration)
    report = evaluate_highlights_delivery(
        probe,
        intervals,
        expected_duration=78.1,
        max_internal_silence=2.8,
    )
    assert intervals[-1] == (56.401, 64.557)
    assert report["accepted"] is False
    assert "unexpected_audio_sample_rate" in report["reasons"]
    assert "long_internal_silence" in report["reasons"]
    assert report["bad_silences"][-1]["duration"] > 8.0


def test_tiny_edge_silence_is_allowed() -> None:
    probe = MediaProbe(
        duration=20.0,
        width=720,
        height=1280,
        audio_sample_rate=48000,
        has_video=True,
        has_audio=True,
    )
    report = evaluate_highlights_delivery(
        probe,
        [(0.0, 0.3), (19.7, 20.0)],
        expected_duration=20.0,
        max_internal_silence=2.8,
    )
    assert report["accepted"] is True
'''

TEST_GEMINI = r'''from pathlib import Path

from services.gemini_model_status import classify_gemini_model


ROOT = Path(__file__).resolve().parents[1]


def test_gemini_36_is_current_not_unknown() -> None:
    diagnostic = classify_gemini_model("gemini-3.6-flash")
    assert diagnostic.level == "info"
    assert "production" in diagnostic.message


def test_shutdown_model_is_error() -> None:
    diagnostic = classify_gemini_model("gemini-2.0-flash")
    assert diagnostic.level == "error"
    assert "gemini-3.6-flash" in diagnostic.message


def test_legacy_25_has_exact_migration_deadline() -> None:
    diagnostic = classify_gemini_model("gemini-2.5-flash")
    assert diagnostic.level == "warning"
    assert "2026-10-16" in diagnostic.message


def test_main_uses_classified_capabilities_and_current_catalog() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "classify_gemini_model" in source
    assert "_required_tools" in source
    assert "_optional_tools" in source
    assert "часть функций молча деградирует" not in source
    assert '"gemini-3.1-flash-lite-preview"' not in source
'''

TEST_PIPELINES = r'''from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_shorts_delivery_uses_measured_timeline_and_actual_trim_range() -> None:
    source = (ROOT / "pipelines" / "shorts.py").read_text(encoding="utf-8")
    assert "probe_media_async(raw_path)" in source
    assert "delivery_duration" in source
    assert "start_seconds=timing.source_start" in source
    assert "end_seconds=timing.source_end" in source
    assert 'duration=max(1, int(round(delivery_duration)))' in source
    assert 'duration=int(c["duration_seconds"])' not in source


def test_verified_highlights_have_final_render_gate() -> None:
    source = (ROOT / "pipelines" / "montage.py").read_text(encoding="utf-8")
    assert "verify_highlights_delivery" in source
    assert "final delivery QA rejected" in source
    assert "speed_applied" in source


def test_highlights_no_longer_auto_merge_by_clock_proximity() -> None:
    source = (ROOT / "services" / "highlights_quality.py").read_text(encoding="utf-8")
    assert "refined = _merge_adjacent_fragments(refined)" not in source
    assert '"actual-transcript-highlights-quality-v2"' in source
    assert 'report["reason"] = f"transcription_error:' in source
'''

_write("services/media_delivery_probe.py", MEDIA_PROBE)
_write("services/gemini_model_status.py", GEMINI_STATUS)
_write("tests/test_media_delivery_probe.py", TEST_MEDIA)
_write("tests/test_gemini_model_status.py", TEST_GEMINI)
_write("tests/test_chat_findings_delivery_contract.py", TEST_PIPELINES)

# --- main.py: honest capability diagnostics and current official model status.
main = _read("main.py")
main = _replace_between(
    main,
    "    _tools = {\n",
    "    # ROUND 39:",
    '''    _required_tools = {
        "ffmpeg": bool(_sh.which("ffmpeg")),
        "ffprobe": bool(_sh.which("ffprobe")),
        "yt-dlp (модуль)": True,
        "yt-dlp JS runtime (Deno>=2.3/Node>=22)": _yt_js_ok,
    }
    _optional_tools = {
        "VOT helper (@vot.js/node)": _vot_helper_ok,
        "vot-cli-live fallback": bool(
            _sh.which("vot-cli-live") or _sh.which("vot-cli-live.cmd")
        ),
    }
    for _tname, _available in _required_tools.items():
        if _available:
            logger.info("🔧 %s: ✅", _tname)
        else:
            logger.warning(
                "🔧 %s: ❌ обязательная возможность недоступна; связанные операции "
                "будут явно отклонены, а не молча ухудшены",
                _tname,
            )
    for _tname, _available in _optional_tools.items():
        if _available:
            logger.info("🔧 %s: ✅", _tname)
        elif _tname == "vot-cli-live fallback" and _vot_helper_ok:
            logger.info(
                "🔧 %s: ⬜ не установлен и не требуется — основной VOT helper доступен",
                _tname,
            )
        else:
            logger.warning(
                "🔧 %s: ⬜ опционально недоступен; LiveDub сообщит точную "
                "недоступную возможность без скрытой деградации",
                _tname,
            )
''',
    label="main capability diagnostics",
)
main = _replace_between(
    main,
    "    # AUDIT L6:",
    '    logger.info(f"🛡',
    '''    from services.gemini_model_status import classify_gemini_model

    _model_diagnostic = classify_gemini_model(GEMINI_MODEL)
    _model_log = logger.info
    if _model_diagnostic.level == "warning":
        _model_log = logger.warning
    elif _model_diagnostic.level == "error":
        _model_log = logger.error
    _model_log(
        "🧠 %s [%s]",
        _model_diagnostic.message,
        _model_diagnostic.policy,
    )
''',
    label="main Gemini model catalog",
)
_write("main.py", main)

# --- Highlights proposal verification: no time-only auto-merge; clip probe mappings.
highlights = _read("services/highlights_quality.py")
highlights = highlights.replace(
    '"policy": "actual-transcript-highlights-quality-v1",',
    '"policy": "actual-transcript-highlights-quality-v2",',
    1,
)
highlights = _replace_once(
    highlights,
    '''    if (
        first_index > 0
        and _needs_left_context(items[first_index]["text"])
        and items[first_index]["start"] - items[first_index - 1]["end"] <= 1.8
        and items[first_index - 1]["start"] >= window_start
    ):
        first_index -= 1
''',
    '''    context_hops = 0
    while (
        first_index > 0
        and context_hops < 3
        and _needs_left_context(items[first_index]["text"])
        and items[first_index]["start"] - items[first_index - 1]["end"] <= 1.8
        and items[first_index - 1]["start"] >= window_start
    ):
        first_index -= 1
        context_hops += 1
''',
    label="Highlights recursive left context",
)
highlights = _replace_between(
    highlights,
    "def _map_probe_segments_to_source(\n",
    "\n\nasync def _judge_actual_transcripts(\n",
    '''def _map_probe_segments_to_source(
    probe_segments: list[dict],
    windows: list[dict],
) -> dict[int, list[dict]]:
    """Map only transcript evidence that is actually inside one probe window."""
    mapped: dict[int, list[dict]] = {int(window["index"]): [] for window in windows}
    items = _normalise_segments(probe_segments)
    for item in items:
        center = (item["start"] + item["end"]) / 2
        window = next(
            (
                candidate
                for candidate in windows
                if candidate["probe_start"] <= center <= candidate["probe_end"]
            ),
            None,
        )
        if window is None:
            continue
        clipped_start = max(float(window["probe_start"]), float(item["start"]))
        clipped_end = min(float(window["probe_end"]), float(item["end"]))
        if clipped_end <= clipped_start:
            continue
        shift = float(window["source_start"]) - float(window["probe_start"])
        words = []
        for word in item.get("words") or []:
            word_start = max(clipped_start, float(word["start"]))
            word_end = min(clipped_end, float(word["end"]))
            if word_end > word_start:
                words.append(
                    {
                        **word,
                        "start": word_start + shift,
                        "end": word_end + shift,
                    }
                )
        if words:
            mapped_text = _clean_text(" ".join(word["word"] for word in words))
        else:
            overhang = (clipped_start - float(item["start"])) + (
                float(item["end"]) - clipped_end
            )
            if overhang > 0.25:
                # A no-word segment crossing a synthetic separator cannot be
                # split safely; accepting its full text would contaminate a cut.
                continue
            mapped_text = item["text"]
        mapped[int(window["index"])].append(
            {
                **item,
                "start": clipped_start + shift,
                "end": clipped_end + shift,
                "text": mapped_text,
                "words": words,
            }
        )
    return mapped
''',
    label="Highlights clipped probe mapping",
)
highlights = _replace_once(
    highlights,
    '''        except asyncio.TimeoutError:
            report["reason"] = "transcription_timeout"
            return None, report
''',
    '''        except asyncio.TimeoutError:
            report["reason"] = "transcription_timeout"
            return None, report
        except Exception as exc:
            report["reason"] = f"transcription_error:{type(exc).__name__}"
            return None, report
''',
    label="Highlights transcription failure evidence",
)
highlights = _replace_once(
    highlights,
    '''    refined = _merge_adjacent_fragments(refined)
    refined, structural_rejections = _drop_overlaps_and_repeats(refined)
''',
    '''    # Do not merge two complete thoughts merely because their source
    # timestamps are close. The old 1.6-second rule could create a new fragment
    # that was never independently verified. Keep the safe pieces separate and
    # let overlap/repetition/coherence gates reject weak combinations.
    refined, structural_rejections = _drop_overlaps_and_repeats(refined)
''',
    label="Highlights time-only merge removal",
)
_write("services/highlights_quality.py", highlights)

# --- Shorts: measured final duration and exact source range own delivery metadata.
shorts = _read("pipelines/shorts.py")
shorts = _replace_once(
    shorts,
    '''    short_trim_save, shorts_speed_get,
    ashorts_speed_get,                            # AUDIT M4
''',
    '''    short_trim_save, shorts_speed_get,
    ashorts_speed_get, get_max_file_size_mb,      # AUDIT M4
''',
    label="Shorts max size import",
)
shorts = _replace_once(
    shorts,
    '''from services.shorts_candidates import create_shorts_candidates
from telegram import InputFile  # AUDIT R25: thumbnail без BufferedReader.name (py3.13)
''',
    '''from services.shorts_candidates import create_shorts_candidates
from services.media_delivery_probe import (
    file_size_mb,
    probe_media_async,
    resolve_delivery_timing,
)
from telegram import InputFile  # AUDIT R25: thumbnail без BufferedReader.name (py3.13)
''',
    label="Shorts media probe imports",
)
shorts = _replace_once(
    shorts,
    '''            if not ok:
                logger.warning(
                    f"Shorts: не удалось вырезать {i}/{total} ({c['start']}–{c['end']})"
                )
                continue

            need_post = do_normalize or (abs(speed - 1.0) > 0.01)
''',
    '''            if not ok:
                logger.warning(
                    f"Shorts: не удалось вырезать {i}/{total} ({c['start']}–{c['end']})"
                )
                continue

            raw_probe = await probe_media_async(raw_path)
            raw_duration = (
                raw_probe.duration
                if raw_probe is not None and raw_probe.duration > 0
                else max(0.001, render_end - render_start)
            )

            need_post = do_normalize or (abs(speed - 1.0) > 0.01)
''',
    label="Shorts raw duration probe",
)
shorts = _replace_between(
    shorts,
    "            need_post = do_normalize or (abs(speed - 1.0) > 0.01)\n",
    "\n            subtitles_applied = False\n",
    '''            need_post = do_normalize or (abs(speed - 1.0) > 0.01)
            current_path = raw_path
            speed_applied = False
            if need_post:
                post_ok = await postprocess_short(
                    raw_path, post_path,
                    normalize_audio=do_normalize,
                    speed=speed,
                )
                if post_ok:
                    current_path = post_path
                    speed_applied = abs(speed - 1.0) > 0.01
                    estimated = raw_duration / speed if speed_applied else raw_duration
                    logger.info(
                        "Shorts %d/%d: обработка OK — raw=%.3fs speed=%s "
                        "expected_delivery=%.3fs",
                        i,
                        total,
                        raw_duration,
                        speed,
                        estimated,
                    )
                else:
                    logger.warning(
                        "Shorts: обработка %d/%d не удалась, использую raw без "
                        "ложного пересчёта speed=%s",
                        i,
                        total,
                        speed,
                    )
''',
    label="Shorts applied speed state",
)
shorts = _replace_once(
    shorts,
    "            thumb_buf = None\n",
    '''            final_probe = await probe_media_async(current_path)
            measured_final_duration = (
                final_probe.duration
                if final_probe is not None and final_probe.duration > 0
                else 0.0
            )
            timing = resolve_delivery_timing(
                source_start=render_start,
                raw_duration=raw_duration,
                source_duration=float(duration or 0),
                speed=speed,
                speed_applied=speed_applied,
                final_duration=measured_final_duration,
            )
            delivery_duration = timing.delivery_duration
            delivery_candidate = {
                **c,
                "_render_start_seconds": timing.source_start,
                "_render_end_seconds": timing.source_end,
                "_raw_duration_seconds": timing.raw_duration,
                "_delivery_duration_seconds": timing.delivery_duration,
                "_speed_applied": timing.speed_applied,
            }
            final_size = file_size_mb(current_path)
            if final_size > get_max_file_size_mb():
                logger.warning(
                    "Shorts %d/%d: финальный файл %.1fMB > %sMB после всех "
                    "этапов — не отправляю заведомо невалидный upload",
                    i,
                    total,
                    final_size,
                    get_max_file_size_mb(),
                )
                if nosub_path:
                    try:
                        nosub_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                continue
            logger.info(
                "Shorts %d/%d delivery evidence: source=%.3f-%.3f raw=%.3fs "
                "final=%.3fs speed_applied=%s size=%.1fMB",
                i,
                total,
                timing.source_start,
                timing.source_end,
                timing.raw_duration,
                timing.delivery_duration,
                timing.speed_applied,
                final_size,
            )

            thumb_buf = None
''',
    label="Shorts final delivery evidence",
)
shorts = shorts.replace('c["title"], c["duration_seconds"],', 'c["title"], delivery_duration,', 1)
shorts = shorts.replace('current_path, snapshot_path, c["duration_seconds"]', 'current_path, snapshot_path, delivery_duration', 1)
shorts = _replace_once(
    shorts,
    '''                    start_seconds=c.get("start_seconds", 0),
                    end_seconds=c.get("end_seconds", 0),
''',
    '''                    start_seconds=timing.source_start,
                    end_seconds=timing.source_end,
''',
    label="Shorts exact trim range",
)
shorts = _replace_once(
    shorts,
    '                    candidate_json=json.dumps(c, ensure_ascii=False),\n',
    '                    candidate_json=json.dumps(delivery_candidate, ensure_ascii=False),\n',
    label="Shorts delivery candidate evidence",
)
shorts = _replace_once(
    shorts,
    '                    duration=int(c["duration_seconds"]),\n',
    '                    duration=max(1, int(round(delivery_duration))),\n',
    label="Shorts Telegram duration",
)
shorts = _replace_once(
    shorts,
    '''                logger.info(
                    f"Shorts: отправлен {i}/{total} ({c['start']}–{c['end']}) '{c['title']}'"
                )
''',
    '''                logger.info(
                    "Shorts: отправлен %d/%d (%s–%s) %r, final=%.3fs",
                    i,
                    total,
                    c["start"],
                    c["end"],
                    c["title"],
                    delivery_duration,
                )
''',
    label="Shorts measured send log",
)
shorts = _replace_once(
    shorts,
    '''            except Exception as send_err:
                logger.warning(f"Shorts: ошибка отправки {i}/{total}: {send_err}")
''',
    '''            except Exception as send_err:
                logger.warning(f"Shorts: ошибка отправки {i}/{total}: {send_err}")
                if nosub_path:
                    try:
                        nosub_path.unlink(missing_ok=True)
                    except Exception:
                        pass
''',
    label="Shorts failed send nosub cleanup",
)
_write("pipelines/shorts.py", shorts)

# --- Montage/Highlights: measured timing, final-size and final-render QA.
montage = _read("pipelines/montage.py")
montage = _replace_once(
    montage,
    '''from converters.md_telegraph import visible_length, safe_trim_caption
from telegram import InputFile  # AUDIT R25: thumbnail без BufferedReader.name (py3.13)
''',
    '''from converters.md_telegraph import visible_length, safe_trim_caption
from services.media_delivery_probe import (
    file_size_mb,
    probe_media_async,
    verify_highlights_delivery,
)
from telegram import InputFile  # AUDIT R25: thumbnail без BufferedReader.name (py3.13)
''',
    label="Montage media probe imports",
)
montage = _replace_once(
    montage,
    '''        if not ok:
            return False

        size_mb = raw_path.stat().st_size / (1024 * 1024) if raw_path.exists() else 0
''',
    '''        if not ok:
            return False

        total_dur = float(cand["total_dur"])
        raw_probe = await probe_media_async(raw_path)
        raw_duration = (
            raw_probe.duration
            if raw_probe is not None and raw_probe.duration > 0
            else max(0.001, total_dur)
        )
        size_mb = file_size_mb(raw_path)
''',
    label="Montage raw media evidence",
)
montage = _replace_between(
    montage,
    '        total_dur = float(cand["total_dur"])\n        need_post = do_normalize or (abs(speed - 1.0) > 0.01)\n',
    "\n        if do_subtitles and HAS_FASTER_WHISPER:\n",
    '''        need_post = do_normalize or (abs(speed - 1.0) > 0.01)
        current_path = raw_path
        speed_applied = False
        if need_post:
            post_ok = await postprocess_short(
                raw_path,
                post_path,
                normalize_audio=do_normalize,
                speed=speed,
            )
            if post_ok:
                current_path = post_path
                speed_applied = abs(speed - 1.0) > 0.01
            else:
                logger.warning(
                    "%s: обработка не удалась; raw будет доставлен без ложного "
                    "пересчёта speed=%s",
                    prefix,
                    speed,
                )

        expected_delivery_duration = (
            raw_duration / speed if speed_applied and speed > 0 else raw_duration
        )
''',
    label="Montage applied speed state",
)
montage = _replace_once(
    montage,
    "                    segments = scale_subtitle_segments(segments, speed)\n",
    "                    segments = scale_subtitle_segments(segments, speed if speed_applied else 1.0)\n",
    label="Montage subtitle speed evidence",
)
montage = _replace_once(
    montage,
    "        thumb_buf = None\n",
    '''        final_probe = await probe_media_async(current_path)
        delivery_duration = (
            final_probe.duration
            if final_probe is not None and final_probe.duration > 0
            else expected_delivery_duration
        )
        final_size = file_size_mb(current_path)
        if final_size > get_max_file_size_mb():
            logger.warning(
                "%s: финальный файл %.1fMB > %sMB после всех этапов — пропускаю",
                prefix,
                final_size,
                get_max_file_size_mb(),
            )
            return False
        if verified_highlights:
            delivery_report = await verify_highlights_delivery(
                current_path,
                expected_duration=expected_delivery_duration,
            )
            if not delivery_report.get("accepted"):
                logger.warning(
                    "%s: final delivery QA rejected: %s",
                    prefix,
                    json.dumps(delivery_report, ensure_ascii=False)[:5000],
                )
                return False
            logger.info(
                "%s: final delivery QA accepted: %s",
                prefix,
                json.dumps(delivery_report, ensure_ascii=False)[:5000],
            )

        thumb_buf = None
''',
    label="Montage final delivery gate",
)
_write("pipelines/montage.py", montage)

# Remove the one-shot implementation mechanism from the resulting tree.
for transient in (
    ROOT / "tools" / "apply_chat_findings_closure.py",
    ROOT / ".github" / "workflows" / "apply-chat-findings-closure.yml",
):
    try:
        transient.unlink()
    except FileNotFoundError:
        pass
