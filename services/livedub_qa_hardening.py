#!/usr/bin/env python3
"""Strict one-to-one confirmation and truthful reporting for LiveDub QA."""
from __future__ import annotations

import os
from pathlib import Path
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
_OLD_LOW_CONFIDENCE_NOTE = (
    "⚠️ Оригинальное аудио было недоступно — сверка велась по конспекту, "
    "не по полному тексту. Часть проповеди проверке не подверглась."
)


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


def _local_file(value: Any) -> bool:
    try:
        return value is not None and Path(value).is_file()
    except (OSError, TypeError, ValueError):
        return False


def _active_part(value: Any) -> bool:
    return value is not None and "ACTIVE" in str(getattr(value, "state", ""))


def _argument(args: tuple, kwargs: dict, index: int, name: str):
    return args[index] if len(args) > index else kwargs.get(name)


def _set_argument(
    args: tuple,
    kwargs: dict,
    index: int,
    name: str,
    value: Any,
) -> tuple[tuple, dict]:
    positional = list(args)
    options = dict(kwargs)
    if len(positional) > index:
        positional[index] = value
        options.pop(name, None)
    else:
        options[name] = value
    return tuple(positional), options


def _exact_original_in_workdir(dub_video: Any) -> Path | None:
    try:
        dub_path = Path(dub_video)
        candidates = sorted(
            dub_path.parent.glob("original_video.*"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        from services.livedub_mix import has_video_stream

        return next(
            (
                candidate
                for candidate in candidates
                if candidate != dub_path and has_video_stream(candidate)
            ),
            None,
        )
    except Exception:
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
        # The focused window has cleaner context than the broad first pass.
        # Once the audible phrase matches, prefer its diagnosis and correction.
        for field in ("heard", "problem", "should_be"):
            if str(match.get(field) or "").strip():
                merged[field] = str(match.get(field) or "").strip()
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


def _insert_after_head(text: str, additions: list[str]) -> str:
    if not additions:
        return text
    lines = str(text or "").splitlines()
    lines[1:1] = additions
    return "\n".join(lines)



def prepare_exact_timeline_inputs(options: dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    """Prefer the untouched source video when available in the LiveDub workdir."""
    prepared = dict(options)
    exact = _exact_original_in_workdir(prepared.get("dub_video_path"))
    if exact is not None:
        prepared["original_audio_path"] = exact
        prepared["existing_audio_part"] = None
        prepared["existing_client"] = None
    return prepared, exact


def annotate_qa_availability(
    result: dict[str, Any],
    options: dict[str, Any],
    exact_original: Path | None,
) -> dict[str, Any]:
    data = dict(result)
    local_original = _local_file(options.get("original_audio_path"))
    reused_original = _active_part(options.get("existing_audio_part")) and options.get("existing_client") is not None
    data["_qa_availability_audited"] = True
    data["_qa_exact_timeline_original"] = exact_original is not None
    data["_qa_original_reference_available"] = bool(local_original or reused_original)
    data["_qa_local_original_available"] = local_original
    data["_qa_reused_original_available"] = reused_original
    data["_qa_russian_audio_available"] = bool(
        _local_file(options.get("dub_audio_path")) or _local_file(options.get("dub_video_path"))
    )
    return data


def decorate_hardened_report(text: str, data: dict[str, Any]) -> str:
    rendered = str(text or "")
    if data.get("_qa_availability_audited"):
        rendered = rendered.replace(_OLD_LOW_CONFIDENCE_NOTE + chr(10), "")
        rendered = rendered.replace(_OLD_LOW_CONFIDENCE_NOTE, "")
        notes: list[str] = []
        if not data.get("_qa_original_reference_available"):
            notes.append(
                "⚠️ Английский оригинал не был доступен как аудио; итог мог "
                "опираться только на ограниченный конспект и не является полной сверкой."
            )
        elif not data.get("_qa_local_original_available") and data.get("_qa_confirmation_failed"):
            notes.append(
                "⚠️ Первый проход видел английский оригинал через Gemini, но "
                "локального файла для точечной перепроверки уже не осталось."
            )
        if not data.get("_qa_russian_audio_available"):
            notes.append(
                "⚠️ Фактически отправленная русская дорожка недоступна для проверки; "
                "оценка и неподтверждённые замечания скрыты."
            )
        rendered = _insert_after_head(rendered, notes)
    if not (data.get("issues") or []):
        rendered = rendered.replace(
            "✅ Показанные замечания прошли точечную перепроверку",
            "✅ Первичные подозрения прошли точечную перепроверку и не подтвердились",
        )
    omitted = int(data.get("_qa_verification_limit_dropped") or 0)
    if omitted:
        rendered = _insert_after_head(
            rendered,
            [
                f"⚠️ За пределами настроенного лимита осталось замечаний: {omitted}; "
                "они не опубликованы и не применены автоматически."
            ],
        )
    return rendered


def install_qa_hardening() -> str:
    """Compatibility validator; hardening is consumed directly by QA/trust."""
    if not callable(confirmed_result_one_to_one):
        raise RuntimeError("strict QA confirmation policy is unavailable")
    return "source-owned one-to-one QA hardening policy"


__all__ = [
    "annotate_qa_availability",
    "confirmed_result_one_to_one",
    "decorate_hardened_report",
    "install_qa_hardening",
    "issues_match_strict",
    "prepare_exact_timeline_inputs",
]
