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


def install_qa_hardening() -> None:
    from services import livedub_qa as module
    import services.livedub_qa_trust as qa

    qa._issues_match = issues_match_strict
    qa._confirmed_result = confirmed_result_one_to_one

    # Long recordings aggregate findings from several segment passes, so the
    # total can exceed the base prompt's per-request limit of ten. Verify twenty
    # by default and allow an explicit operator limit up to forty.
    os.environ.setdefault("LIVEDUB_QA_VERIFY_MAX_ISSUES", "20")
    original_env_int = qa._env_int
    if not getattr(original_env_int, "_mp3bot_qa_limit_40", False):
        def qa_env_int(name: str, default: int, low: int, high: int) -> int:
            if name == "LIVEDUB_QA_VERIFY_MAX_ISSUES":
                return original_env_int(name, 20, 1, 40)
            return original_env_int(name, default, low, high)

        qa_env_int._mp3bot_qa_limit_40 = True  # type: ignore[attr-defined]
        qa._env_int = qa_env_int

    current_verify = qa._verify_candidate_windows
    if not getattr(current_verify, "_mp3bot_limit_safe", False):
        async def verify_limit_safe(original_run, *, primary, **kwargs):
            all_issues = [
                dict(item)
                for item in (primary.get("issues") or [])
                if isinstance(item, dict)
            ]
            max_issues = qa._env_int("LIVEDUB_QA_VERIFY_MAX_ISSUES", 20, 1, 40)
            selected = all_issues[:max_issues]
            limited_primary = dict(primary)
            limited_primary["issues"] = selected
            result = await current_verify(
                original_run,
                primary=limited_primary,
                **kwargs,
            )
            omitted = max(0, len(all_issues) - len(selected))
            if omitted:
                result["_qa_candidate_count_total"] = len(all_issues)
                result["_qa_verification_limit_dropped"] = omitted
                result["_qa_unconfirmed_dropped"] = (
                    int(result.get("_qa_unconfirmed_dropped") or 0) + omitted
                )
                result["_low_confidence"] = True
            return result

        verify_limit_safe._mp3bot_limit_safe = True  # type: ignore[attr-defined]
        qa._verify_candidate_windows = verify_limit_safe

    current_run = module.run_translation_qa
    if not getattr(current_run, "_mp3bot_availability_audit", False):
        async def audited_run(*args, **kwargs):
            call_args, call_kwargs = tuple(args), dict(kwargs)
            dub_video_before = _argument(call_args, call_kwargs, 0, "dub_video_path")
            exact_original = _exact_original_in_workdir(dub_video_before)
            if exact_original is not None:
                # The ordinary MP3 may have SponsorBlock cuts or other timeline
                # transforms. Use the untouched downloaded source video for both
                # the full pass and every focused window, and do not reuse a
                # Gemini upload that may represent the edited MP3 timeline.
                call_args, call_kwargs = _set_argument(
                    call_args, call_kwargs, 1, "original_audio_path", exact_original
                )
                call_args, call_kwargs = _set_argument(
                    call_args, call_kwargs, 7, "existing_audio_part", None
                )
                call_args, call_kwargs = _set_argument(
                    call_args, call_kwargs, 8, "existing_client", None
                )

            result = await current_run(*call_args, **call_kwargs)
            if not isinstance(result, dict):
                return result
            original_audio = _argument(
                call_args, call_kwargs, 1, "original_audio_path"
            )
            dub_video = _argument(call_args, call_kwargs, 0, "dub_video_path")
            dub_audio = _argument(call_args, call_kwargs, 6, "dub_audio_path")
            existing_part = _argument(
                call_args, call_kwargs, 7, "existing_audio_part"
            )
            existing_client = _argument(
                call_args, call_kwargs, 8, "existing_client"
            )
            local_original = _local_file(original_audio)
            reused_original = _active_part(existing_part) and existing_client is not None
            result["_qa_availability_audited"] = True
            result["_qa_exact_timeline_original"] = exact_original is not None
            result["_qa_original_reference_available"] = bool(
                local_original or reused_original
            )
            result["_qa_local_original_available"] = local_original
            result["_qa_reused_original_available"] = reused_original
            result["_qa_russian_audio_available"] = bool(
                _local_file(dub_audio) or _local_file(dub_video)
            )
            return result

        audited_run._mp3bot_availability_audit = True  # type: ignore[attr-defined]
        module.run_translation_qa = audited_run

    current_notes = qa._insert_report_notes
    if getattr(current_notes, "_mp3bot_deep_wording", False):
        return

    def notes(text: str, data: dict[str, Any]) -> str:
        rendered = current_notes(text, data)
        if data.get("_qa_availability_audited"):
            rendered = rendered.replace(_OLD_LOW_CONFIDENCE_NOTE + "\n", "")
            rendered = rendered.replace(_OLD_LOW_CONFIDENCE_NOTE, "")
            availability_notes: list[str] = []
            if not data.get("_qa_original_reference_available"):
                availability_notes.append(
                    "⚠️ Английский оригинал не был доступен как аудио; итог мог "
                    "опираться только на ограниченный конспект и не является полной сверкой."
                )
            elif (
                not data.get("_qa_local_original_available")
                and data.get("_qa_confirmation_failed")
            ):
                availability_notes.append(
                    "⚠️ Первый проход видел английский оригинал через Gemini, но "
                    "локального файла для точечной перепроверки уже не осталось."
                )
            if not data.get("_qa_russian_audio_available"):
                availability_notes.append(
                    "⚠️ Фактически отправленная русская дорожка недоступна для проверки; "
                    "оценка и неподтверждённые замечания скрыты."
                )
            rendered = _insert_after_head(rendered, availability_notes)
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

    notes._mp3bot_deep_wording = True  # type: ignore[attr-defined]
    qa._insert_report_notes = notes
