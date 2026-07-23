#!/usr/bin/env python3
"""Conservative trust guard for LiveDub semantic QA.

The first QA pass scans the complete recording (segmented for long media). A
second full scan was both expensive and weaker than it looked: it repeated the
same prompt over the same material and could repeat the same hallucination.
This guard verifies only candidate regions:

* SRT is never accepted as proof of what the user heard;
* the first pass compares actual English and Russian audio;
* candidate findings are grouped into short time windows and rechecked with the
  main quality model on freshly extracted audio clips;
* only findings independently rediscovered in those focused windows survive;
* if a clean Russian track or local English original is unavailable, candidate
  issues are hidden rather than presented or used for destructive auto-muting;
* a bilingual final mix may still be checked, but all findings are downgraded to
  minor and the report is explicitly marked low-confidence.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_INSTALL_LOCK = threading.Lock()
_TRUE = {"1", "true", "yes", "on"}
_STOPWORDS = {
    "была", "было", "были", "быть", "весь", "вместо", "для", "его", "если", "есть",
    "или", "как", "который", "между", "может", "нужно", "один", "она", "они", "оно",
    "оригинал", "перевод", "переводе", "перевода", "русский", "своих", "смысл", "слово",
    "так", "того", "только", "фраза", "что", "это", "этот",
}


def _enabled() -> bool:
    return os.getenv("LIVEDUB_QA_AUDIO_TRUST", "1").strip().lower() in _TRUE


def _confirmation_enabled() -> bool:
    return os.getenv("LIVEDUB_QA_CONFIRM_ISSUES", "1").strip().lower() in _TRUE


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip() or str(default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high))


def _clock_seconds(value: Any) -> float | None:
    parts = str(value or "").strip().split(":")
    try:
        if len(parts) == 2:
            return float(int(parts[0]) * 60 + int(parts[1]))
        if len(parts) == 3:
            return float(int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]))
    except (TypeError, ValueError):
        return None
    return None


def _format_clock(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _issue_tokens(issue: dict[str, Any]) -> set[str]:
    # The proposed correction is excluded deliberately: unrelated findings can
    # receive the same generic should_be wording and must not confirm each other.
    text = " ".join(str(issue.get(key) or "") for key in ("heard", "problem")).casefold()
    return {
        token
        for token in re.findall(r"[a-zа-яё]{4,}", text)
        if token not in _STOPWORDS
    }


def _issues_match(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_time = _clock_seconds(first.get("time"))
    second_time = _clock_seconds(second.get("time"))
    if first_time is None or second_time is None:
        return False
    distance = abs(first_time - second_time)
    if distance > 18:
        return False

    left = _issue_tokens(first)
    right = _issue_tokens(second)
    common = left & right
    union = left | right
    similarity = len(common) / max(1, len(union))
    return bool(
        (distance <= 6 and len(common) >= 1)
        or len(common) >= 2
        or similarity >= 0.28
    )


def _clean_issues(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in (value or []) if isinstance(item, dict)]


def _numeric_score(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _confirmed_result(primary: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    first_issues = _clean_issues(primary.get("issues"))
    second_issues = _clean_issues(validation.get("issues"))
    confirmed: list[dict[str, Any]] = []

    for issue in first_issues:
        match = next((candidate for candidate in second_issues if _issues_match(issue, candidate)), None)
        if match is None:
            continue
        merged = dict(issue)
        # Auto-muting is destructive, so both passes must independently vote major.
        merged["severity"] = (
            "major"
            if str(issue.get("severity")) == "major" and str(match.get("severity")) == "major"
            else "minor"
        )
        if str(match.get("heard") or "").strip():
            merged["heard"] = str(match.get("heard") or "").strip()
        confirmed.append(merged)

    result = dict(primary)
    result["issues"] = confirmed
    result["_qa_audio_grounded"] = True
    result["_qa_confirmation_passes"] = 2
    result["_qa_candidate_count"] = len(first_issues)
    result["_qa_unconfirmed_dropped"] = max(0, len(first_issues) - len(confirmed))

    first_score = _numeric_score(primary.get("score"))
    second_score = _numeric_score(validation.get("score"))
    if confirmed and first_score is not None and second_score is not None:
        result["score"] = round((first_score + second_score) / 2)
    elif not confirmed:
        result.pop("score", None)

    majors = sum(1 for item in confirmed if str(item.get("severity")) == "major")
    if confirmed:
        major_note = f", серьёзных — {majors}" if majors else ""
        result["verdict"] = (
            "Полная аудиопроверка и точечная перепроверка подтвердили "
            f"{len(confirmed)} неточностей{major_note}."
        )
    else:
        result["verdict"] = (
            "Точечная аудиопроверка не подтвердила искажений смысла, "
            "предположенных первым проходом."
        )
    result["reasoning"] = (
        "Сначала сравнивались фактически звучащие английская и русская дорожки по всей записи. "
        "Затем подозрительные места были заново вырезаны и проверены отдельными короткими аудиоокнами."
    )
    return result


def _unconfirmed_failure_result(primary: dict[str, Any], reason: str = "") -> dict[str, Any]:
    first_issues = _clean_issues(primary.get("issues"))
    result = dict(primary)
    result["issues"] = []
    result.pop("score", None)
    result["_qa_audio_grounded"] = False
    result["_qa_confirmation_passes"] = 1
    result["_qa_confirmation_failed"] = True
    result["_qa_candidate_count"] = len(first_issues)
    result["_qa_unconfirmed_dropped"] = len(first_issues)
    result["_low_confidence"] = True
    detail = str(reason or "").strip()
    result["verdict"] = (
        "Первый проход нашёл возможные неточности, но надёжная точечная "
        "аудиопроверка не завершилась; выводы скрыты и автоматические исправления не применены."
        + (f" Причина: {detail}." if detail else "")
    )
    return result


def _active_audio_part(value: Any) -> bool:
    return value is not None and "ACTIVE" in str(getattr(value, "state", ""))


def _local_path(value: Any) -> Path | None:
    try:
        path = Path(value) if value is not None else None
        return path if path is not None and path.is_file() else None
    except (OSError, TypeError, ValueError):
        return None


def _candidate_windows(
    issues: list[dict[str, Any]],
    duration: int,
    *,
    max_issues: int,
    before_sec: int,
    after_sec: int,
) -> list[tuple[int, int]]:
    points = sorted(
        int(sec)
        for sec in (_clock_seconds(issue.get("time")) for issue in issues[:max_issues])
        if sec is not None and sec >= 0
    )
    windows: list[list[int]] = []
    total = max(1, int(duration or 0))
    for point in points:
        start = max(0, point - before_sec)
        end = min(total, point + after_sec)
        if end <= start:
            end = min(total, start + max(20, before_sec + after_sec))
        if windows and start <= windows[-1][1] + 8:
            windows[-1][1] = max(windows[-1][1], end)
        else:
            windows.append([start, end])
    return [(start, max(1, end - start)) for start, end in windows]


def _offset_validation_issues(result: dict[str, Any], start: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for issue in _clean_issues(result.get("issues")):
        local = _clock_seconds(issue.get("time"))
        if local is None:
            continue
        item = dict(issue)
        item["time"] = _format_clock(start + local)
        out.append(item)
    return out


async def _verify_candidate_windows(
    original_run,
    *,
    primary: dict[str, Any],
    dub_video_path: Path,
    original_audio_path: Optional[Path],
    duration: int,
    model_name: str,
    dub_audio_path: Optional[Path],
) -> dict[str, Any]:
    original_source = _local_path(original_audio_path)
    clean_ru = _local_path(dub_audio_path)
    russian_source = clean_ru or _local_path(dub_video_path)
    if original_source is None:
        return _unconfirmed_failure_result(primary, "локальный английский оригинал недоступен")
    if russian_source is None:
        return _unconfirmed_failure_result(primary, "фактически отправленная русская дорожка недоступна")

    issues = _clean_issues(primary.get("issues"))
    max_issues = _env_int("LIVEDUB_QA_VERIFY_MAX_ISSUES", 8, 1, 16)
    before_sec = _env_int("LIVEDUB_QA_VERIFY_BEFORE_SEC", 14, 5, 45)
    after_sec = _env_int("LIVEDUB_QA_VERIFY_AFTER_SEC", 30, 10, 75)
    windows = _candidate_windows(
        issues,
        duration,
        max_issues=max_issues,
        before_sec=before_sec,
        after_sec=after_sec,
    )
    if not windows:
        return _unconfirmed_failure_result(primary, "у замечаний нет корректных таймкодов")

    verify_model = os.getenv("LIVEDUB_QA_VERIFY_MODEL", "").strip()
    if not verify_model:
        try:
            from core.database import GEMINI_MODEL

            verify_model = GEMINI_MODEL
        except Exception:
            verify_model = model_name
    verify_thinking = os.getenv("LIVEDUB_QA_VERIFY_THINKING", "low").strip() or "low"

    validations: list[dict[str, Any]] = []
    successful_windows = 0
    with tempfile.TemporaryDirectory(prefix="mp3bot-qa-verify-") as temp_dir:
        root = Path(temp_dir)
        try:
            from services.livedub_long_qa import _extract_audio_segment
        except Exception as exc:
            return _unconfirmed_failure_result(primary, f"экстрактор аудиоокон недоступен: {exc}")

        for index, (start, length) in enumerate(windows, start=1):
            try:
                logger.info(
                    "[LiveDubQATrust] focused window %d/%d: %s–%s",
                    index,
                    len(windows),
                    _format_clock(start),
                    _format_clock(start + length),
                )
                original_clip = await asyncio.to_thread(
                    _extract_audio_segment,
                    original_source,
                    start,
                    length,
                    root / f"original-{index:02d}.mp3",
                )
                russian_clip = await asyncio.to_thread(
                    _extract_audio_segment,
                    russian_source,
                    start,
                    length,
                    root / f"russian-{index:02d}.mp3",
                )
                checked = await original_run(
                    dub_video_path=russian_clip,
                    original_audio_path=original_clip,
                    ai_data=None,
                    duration=length,
                    model_name=verify_model or model_name,
                    dub_srt_path=None,
                    dub_audio_path=russian_clip,
                    existing_audio_part=None,
                    existing_client=None,
                    thinking_level=verify_thinking,
                )
                if isinstance(checked, dict):
                    successful_windows += 1
                    validations.extend(_offset_validation_issues(checked, start))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[LiveDubQATrust] focused window %d failed: %s", index, str(exc)[:220])

    if successful_windows == 0:
        return _unconfirmed_failure_result(primary, "ни одно проверочное аудиоокно не завершилось")

    # An empty issue list from a completed window is valid evidence that the
    # candidate was not reproduced — it is not a verifier failure.
    result = _confirmed_result(primary, {"issues": validations})
    if result.get("issues") and _numeric_score(primary.get("score")) is not None:
        # Window scores describe only suspicious excerpts, not the whole sermon.
        result["score"] = round(float(primary["score"]))
    result["_qa_verification_windows"] = successful_windows
    result["_qa_verification_windows_total"] = len(windows)
    result["_qa_clean_russian_track"] = clean_ru is not None
    if successful_windows < len(windows):
        result["_qa_verification_partial"] = True
        result["_low_confidence"] = True

    if clean_ru is None:
        # The final mix contains quiet English beneath Russian. It is useful for
        # review, but not safe enough for destructive automatic muting.
        for issue in result.get("issues") or []:
            issue["severity"] = "minor"
        result["_qa_mixed_audio"] = True
        result["_low_confidence"] = True
        if result.get("issues"):
            result["verdict"] = (
                "Точечная проверка смешанной дорожки подтвердила "
                f"{len(result.get('issues') or [])} возможных неточностей; "
                "автоматическое приглушение отключено."
            )
    return result


def _insert_report_notes(text: str, qa: dict[str, Any]) -> str:
    lines = str(text or "").splitlines()
    notes: list[str] = []
    if qa.get("_qa_audio_grounded"):
        notes.append(
            "🎧 Сверка выполнена по фактически звучащим английской и русской "
            "дорожкам; SRT не использовался как источник истины."
        )
    if int(qa.get("_qa_confirmation_passes") or 0) >= 2:
        checked = int(qa.get("_qa_verification_windows") or 0)
        total = int(qa.get("_qa_verification_windows_total") or checked)
        suffix = f" ({checked}/{total} аудиоокон)." if total else "."
        notes.append("✅ Показанные замечания прошли точечную перепроверку" + suffix)
    if qa.get("_qa_verification_partial"):
        notes.append(
            "⚠️ Не все проверочные окна завершились; выводы из непроверенных мест отброшены."
        )
    if qa.get("_qa_mixed_audio"):
        notes.append(
            "⚠️ Чистой русской дорожки не было: проверялся финальный микс с тихим оригиналом; "
            "замечания понижены до предварительных и автоисправление отключено."
        )
    dropped = int(qa.get("_qa_unconfirmed_dropped") or 0)
    if dropped:
        notes.append(f"🧹 Неподтверждённых замечаний отброшено: {dropped}.")
    if qa.get("_qa_confirmation_failed"):
        notes.append("⚠️ Надёжная точечная перепроверка не завершилась; подозрения скрыты.")
    if not notes:
        return text

    insert_at = 1 if lines else 0
    lines[insert_at:insert_at] = notes
    joined = "\n".join(lines)
    try:
        from converters.md_telegraph import safe_trim_caption

        return safe_trim_caption(joined, 3900)
    except Exception:
        return joined[:3900]


def install_livedub_qa_trust() -> None:
    """Wrap the already-installed short/long QA implementation once."""
    if not _enabled():
        return
    with _INSTALL_LOCK:
        from services import livedub_qa as module

        original_run = module.run_translation_qa
        original_format = module.format_qa_report
        if getattr(original_run, "_mp3bot_audio_trust", False):
            return

        async def wrapped_run(
            dub_video_path: Path,
            original_audio_path: Optional[Path],
            ai_data: Optional[dict],
            duration: int,
            model_name: str = "",
            dub_srt_path: Optional[Path] = None,
            dub_audio_path: Optional[Path] = None,
            existing_audio_part=None,
            existing_client=None,
            thinking_level: str = "high",
        ) -> Optional[dict]:
            common = dict(
                dub_video_path=dub_video_path,
                original_audio_path=original_audio_path,
                ai_data=ai_data,
                duration=duration,
                model_name=model_name,
                # Critical trust boundary: compare what the user actually hears.
                dub_srt_path=None,
                dub_audio_path=dub_audio_path,
                existing_audio_part=existing_audio_part,
                existing_client=existing_client,
                thinking_level=thinking_level,
            )
            primary = await original_run(**common)
            if not isinstance(primary, dict):
                return primary
            primary = dict(primary)

            original_available = bool(
                _local_path(original_audio_path)
                or (_active_audio_part(existing_audio_part) and existing_client is not None)
            )
            russian_available = bool(_local_path(dub_audio_path) or _local_path(dub_video_path))
            primary["_qa_audio_grounded"] = original_available and russian_available
            primary["_qa_confirmation_passes"] = 1
            if not primary["_qa_audio_grounded"]:
                primary["_low_confidence"] = True

            issues = _clean_issues(primary.get("issues"))
            if not issues or not _confirmation_enabled():
                return primary
            if not original_available or not russian_available:
                return _unconfirmed_failure_result(primary, "нет обеих фактически звучащих дорожек")

            logger.info(
                "[LiveDubQATrust] %d candidate issue(s): focused audio verification",
                len(issues),
            )
            result = await _verify_candidate_windows(
                original_run,
                primary=primary,
                dub_video_path=Path(dub_video_path),
                original_audio_path=original_audio_path,
                duration=int(duration or 0),
                model_name=model_name,
                dub_audio_path=dub_audio_path,
            )
            logger.info(
                "[LiveDubQATrust] confirmed=%d dropped=%d windows=%d/%d",
                len(result.get("issues") or []),
                int(result.get("_qa_unconfirmed_dropped") or 0),
                int(result.get("_qa_verification_windows") or 0),
                int(result.get("_qa_verification_windows_total") or 0),
            )
            return result

        def wrapped_format(qa: dict, video_url: str = "") -> str:
            return _insert_report_notes(original_format(qa, video_url=video_url), dict(qa or {}))

        wrapped_run._mp3bot_audio_trust = True  # type: ignore[attr-defined]
        wrapped_format._mp3bot_audio_trust = True  # type: ignore[attr-defined]
        module.run_translation_qa = wrapped_run
        module.format_qa_report = wrapped_format
        logger.info("🎧 LiveDub QA trust: full scan + focused audio verification enabled")
