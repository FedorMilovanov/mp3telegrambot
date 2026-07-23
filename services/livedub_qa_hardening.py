#!/usr/bin/env python3
"""Strict one-to-one confirmation for LiveDub audio QA findings."""
from __future__ import annotations

import re
from typing import Any

_GENERIC = {
    "ошибка", "ошибку", "ошибки", "неверная", "неверно", "неточная", "неточно",
    "цитата", "цитате", "цитаты", "искажение", "искажено", "формулировка",
    "перевод", "смысл", "фраза", "проблема", "место", "текст",
}
_STOP = {
    "была", "было", "были", "быть", "весь", "вместо", "для", "его", "если",
    "есть", "или", "как", "который", "между", "может", "нужно", "один", "она",
    "они", "оно", "оригинал", "перевод", "переводе", "перевода", "русский",
    "своих", "смысл", "слово", "так", "того", "только", "фраза", "что", "это",
    "этот",
}


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zа-яё]{4,}", str(value or "").casefold())
        if token not in _STOP
    }


def _seconds(value: Any) -> float | None:
    parts = str(value or "").strip().split(":")
    try:
        if len(parts) == 2:
            return float(int(parts[0]) * 60 + int(parts[1]))
        if len(parts) == 3:
            return float(int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]))
    except (TypeError, ValueError):
        return None
    return None


def issues_match_strict(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_time, second_time = _seconds(first.get("time")), _seconds(second.get("time"))
    if first_time is None or second_time is None:
        return False
    distance = abs(first_time - second_time)
    if distance > 18:
        return False

    left, right = _tokens(first.get("heard")), _tokens(second.get("heard"))
    common, union = left & right, left | right
    similarity = len(common) / max(1, len(union))
    if left and right:
        return bool(
            len(common) >= 2
            or similarity >= 0.32
            or (distance <= 5 and len(common) >= 1 and similarity >= 0.18)
        )

    # Problem wording is only a fallback if one pass omitted an audible quote.
    left_problem = _tokens(first.get("problem")) - _GENERIC
    right_problem = _tokens(second.get("problem")) - _GENERIC
    return distance <= 5 and len(left_problem & right_problem) >= 2


def confirmed_result_one_to_one(
    primary: dict[str, Any], validation: dict[str, Any]
) -> dict[str, Any]:
    first = [dict(item) for item in primary.get("issues") or [] if isinstance(item, dict)]
    remaining = [
        dict(item) for item in validation.get("issues") or [] if isinstance(item, dict)
    ]
    confirmed: list[dict[str, Any]] = []
    for issue in first:
        match_index = next(
            (
                index
                for index, candidate in enumerate(remaining)
                if issues_match_strict(issue, candidate)
            ),
            None,
        )
        if match_index is None:
            continue
        match = remaining.pop(match_index)  # one validation cannot confirm two findings
        merged = dict(issue)
        merged["severity"] = (
            "major"
            if str(issue.get("severity")) == "major"
            and str(match.get("severity")) == "major"
            else "minor"
        )
        if str(match.get("heard") or "").strip():
            merged["heard"] = str(match.get("heard") or "").strip()
        confirmed.append(merged)

    result = dict(primary)
    result["issues"] = confirmed
    result["_qa_audio_grounded"] = True
    result["_qa_confirmation_passes"] = 2
    result["_qa_candidate_count"] = len(first)
    result["_qa_unconfirmed_dropped"] = max(0, len(first) - len(confirmed))
    if not confirmed:
        result.pop("score", None)
        result["verdict"] = (
            "Точечная аудиопроверка не подтвердила искажений смысла, "
            "предположенных первым проходом."
        )
    else:
        majors = sum(
            1 for item in confirmed if str(item.get("severity")) == "major"
        )
        suffix = f", серьёзных — {majors}" if majors else ""
        result["verdict"] = (
            "Полная аудиопроверка и точечная перепроверка подтвердили "
            f"{len(confirmed)} неточностей{suffix}."
        )
    result["reasoning"] = (
        "Сначала сравнивались фактически звучащие английская и русская дорожки "
        "по всей записи. Затем подозрительные места были заново вырезаны и "
        "проверены отдельными аудиоокнами."
    )
    return result


def install_qa_hardening() -> None:
    import services.livedub_qa_trust as qa

    qa._issues_match = issues_match_strict
    qa._confirmed_result = confirmed_result_one_to_one
    current_notes = qa._insert_report_notes
    if getattr(current_notes, "_mp3bot_deep_wording", False):
        return

    def notes(text: str, data: dict[str, Any]) -> str:
        rendered = current_notes(text, data)
        if not (data.get("issues") or []):
            rendered = rendered.replace(
                "✅ Показанные замечания прошли точечную перепроверку",
                "✅ Первичные подозрения прошли точечную перепроверку и не подтвердились",
            )
        return rendered

    notes._mp3bot_deep_wording = True  # type: ignore[attr-defined]
    qa._insert_report_notes = notes
