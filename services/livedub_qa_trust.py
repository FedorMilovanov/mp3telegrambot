#!/usr/bin/env python3
"""Trust guard for LiveDub semantic QA.

The previous QA path preferred Yandex SRT whenever it existed and explicitly
instructed Gemini to quote ``heard`` from that text.  That is useful for speed,
but it can report a phrase that is absent from the audio actually delivered to
the user when subtitles and synthesis differ.  This adapter makes the audible
English and Russian tracks the source of truth.

For every run:
* SRT is removed from the semantic comparison input;
* the existing short/segmented QA implementation compares audio to audio;
* when the first pass finds issues, a second independent pass must confirm each
  issue before it is shown or used by automatic muting.
"""
from __future__ import annotations

import logging
import math
import os
import re
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


def _issue_tokens(issue: dict[str, Any]) -> set[str]:
    text = " ".join(
        str(issue.get(key) or "")
        for key in ("heard", "problem", "should_be")
    ).casefold()
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
    if distance > 25:
        return False

    left = _issue_tokens(first)
    right = _issue_tokens(second)
    common = left & right
    union = left | right
    similarity = len(common) / max(1, len(union))

    # Very close timestamps can confirm a concise issue whose wording changed;
    # otherwise require meaningful lexical agreement across the two passes.
    return bool(
        (distance <= 8 and len(common) >= 1)
        or len(common) >= 2
        or similarity >= 0.22
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
        # A destructive automatic fix is allowed only when both passes called
        # the finding major.  Disagreement is retained as a minor warning.
        merged["severity"] = (
            "major"
            if str(issue.get("severity")) == "major" and str(match.get("severity")) == "major"
            else "minor"
        )
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
        # Do not display a low numerical score that was based entirely on
        # findings the confirmation pass rejected.
        result.pop("score", None)

    majors = sum(1 for item in confirmed if str(item.get("severity")) == "major")
    if confirmed:
        major_note = f", серьёзных — {majors}" if majors else ""
        result["verdict"] = (
            f"Два независимых аудиопрохода подтвердили {len(confirmed)} "
            f"неточностей{major_note}."
        )
    else:
        result["verdict"] = (
            "Повторная аудиопроверка не подтвердила искажений смысла, "
            "найденных первым проходом."
        )
    result["reasoning"] = (
        "Сначала сравнивались фактически звучащие английская и русская дорожки. "
        "Каждое показанное замечание затем проверялось повторным независимым проходом."
    )
    return result


def _unconfirmed_failure_result(primary: dict[str, Any]) -> dict[str, Any]:
    first_issues = _clean_issues(primary.get("issues"))
    result = dict(primary)
    result["issues"] = []
    result.pop("score", None)
    result["_qa_audio_grounded"] = True
    result["_qa_confirmation_passes"] = 1
    result["_qa_confirmation_failed"] = True
    result["_qa_candidate_count"] = len(first_issues)
    result["_qa_unconfirmed_dropped"] = len(first_issues)
    result["_low_confidence"] = True
    result["verdict"] = (
        "Первый проход нашёл возможные неточности, но повторная аудиопроверка "
        "не завершилась; неподтверждённые выводы и автоматические исправления не применены."
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
        notes.append("✅ Каждое показанное замечание подтверждено повторным аудиопроходом.")
    dropped = int(qa.get("_qa_unconfirmed_dropped") or 0)
    if dropped:
        notes.append(f"🧹 Неподтверждённых замечаний отброшено: {dropped}.")
    if qa.get("_qa_confirmation_failed"):
        notes.append("⚠️ Повторный проход не завершился; неподтверждённые замечания скрыты.")
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
            primary["_qa_audio_grounded"] = True
            primary["_qa_confirmation_passes"] = 1

            issues = _clean_issues(primary.get("issues"))
            if not issues or not _confirmation_enabled():
                return primary

            logger.info(
                "[LiveDubQATrust] %d candidate issue(s): starting independent confirmation pass",
                len(issues),
            )
            validation = await original_run(**common)
            if not isinstance(validation, dict):
                logger.warning("[LiveDubQATrust] confirmation pass returned no result")
                return _unconfirmed_failure_result(primary)

            result = _confirmed_result(primary, validation)
            logger.info(
                "[LiveDubQATrust] confirmed=%d dropped=%d",
                len(result.get("issues") or []),
                int(result.get("_qa_unconfirmed_dropped") or 0),
            )
            return result

        def wrapped_format(qa: dict, video_url: str = "") -> str:
            return _insert_report_notes(original_format(qa, video_url=video_url), dict(qa or {}))

        wrapped_run._mp3bot_audio_trust = True  # type: ignore[attr-defined]
        wrapped_format._mp3bot_audio_trust = True  # type: ignore[attr-defined]
        module.run_translation_qa = wrapped_run
        module.format_qa_report = wrapped_format
        logger.info("🎧 LiveDub QA trust: audio-to-audio + confirmed findings enabled")
