#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Semantic rescue overlay for persistent foreign-language VoxCPM output.

Focused QA v4.6 remains the normal path. When a segment has already exhausted
five focused rounds and local Whisper still hears a foreign-script fragment
instead of Russian, this overlay preserves all accepted checkpoints and reruns
only those IDs through an explicit prompt-transcript VoxCPM mode.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from tools.voxcpm2 import semantic_tts_guard as legacy
from tools.voxcpm2 import semantic_tts_guard_v4 as base
from tools.voxcpm2 import semantic_tts_guard_v46 as focused

_POLICY = "semantic-prompt-rescue-v4.7"
_RESCUE_ROUNDS = 2
_ORIGINAL_FOCUSED_RUN = focused._run_quality_synth_v46
_INSTALLED = False
_FOREIGN_SCRIPT_RE = re.compile(
    r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff"
    r"\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]"
)


def log(message: str) -> None:
    print(f"[TTS-QA-V4.7] {message}", flush=True)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_segments(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [dict(item) for item in payload if isinstance(item, dict)]


def _marker_failed_ids(marker: dict[str, Any]) -> set[int]:
    return {
        int(value)
        for value in marker.get("failed_segment_ids", [])
        if str(value).isdigit()
    }


def _prompt_leak_ids(report: dict[str, Any]) -> set[int]:
    failed = {
        int(value)
        for value in report.get("failed_segment_ids", [])
        if str(value).isdigit()
    }
    result: set[int] = set()
    for check in report.get("segments", []):
        if not isinstance(check, dict):
            continue
        try:
            segment_id = int(check.get("id"))
        except (TypeError, ValueError):
            continue
        if segment_id not in failed:
            continue
        semantic = check.get("semantic")
        if not isinstance(semantic, dict) or semantic.get("passed", True):
            continue
        heard = str(semantic.get("heard") or "").strip()
        recall = float(semantic.get("token_recall") or 0.0)
        similarity = float(semantic.get("sequence_similarity") or 0.0)
        foreign = bool(semantic.get("foreign_language"))
        non_russian_script = bool(_FOREIGN_SCRIPT_RE.search(heard))
        if (
            foreign
            or non_russian_script
            or (heard and recall <= 0.05 and similarity <= 0.05)
        ):
            result.add(segment_id)
    return result


def _rescue_ids(
    report: dict[str, Any],
    marker: dict[str, Any],
) -> set[int]:
    leaks = _prompt_leak_ids(report)
    if leaks:
        return leaks
    state = str(marker.get("state") or "")
    if state.startswith("partial_semantic_rescue"):
        return _marker_failed_ids(marker)
    return set()


def _prompt_texts(
    command: Sequence[str],
    guard_dir: Path,
) -> Path:
    destination = guard_dir / "reference_prompt_texts_v47.json"
    existing = _load_json(destination)
    if all(
        str(existing.get(name) or "").strip()
        for name in ("extended", "composite")
    ):
        return destination

    payload: dict[str, str] = {}
    for profile, flag in (
        ("extended", "--extended-reference"),
        ("composite", "--composite-reference"),
    ):
        reference = Path(legacy._flag_value(command, flag)).resolve()
        heard, _language, _probability = legacy._transcribe(
            reference,
            language="en",
        )
        heard = str(heard or "").strip()
        if not heard:
            raise RuntimeError(
                f"Whisper не распознал {profile} voice reference "
                "для semantic rescue."
            )
        payload[profile] = heard

    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log("точные английские prompt transcripts подготовлены и сохранены")
    return destination


def _adapt_rescue_segments(
    path: Path,
    segments: list[dict[str, Any]],
    failed_ids: Iterable[int],
    *,
    rescue_round: int,
) -> None:
    failed = {int(value) for value in failed_ids}
    changed = False
    for item in segments:
        try:
            segment_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if segment_id not in failed:
            continue

        current = str(item.get("reference_profile") or "extended")
        if rescue_round % 2 == 1:
            profile = "composite" if current == "extended" else "extended"
        else:
            profile = "extended" if current == "composite" else "composite"
        item["reference_profile"] = profile
        item["tail_guard"] = max(
            float(item.get("tail_guard") or 0.18),
            0.24,
        )
        adaptations = list(item.get("qa_adaptations") or [])
        adaptations.extend(
            [
                "semantic_prompt_transcript",
                "forced_silent_tail",
                f"rescue_reference:{profile}",
                f"rescue_round:{rescue_round}",
            ]
        )
        item["qa_adaptations"] = list(dict.fromkeys(adaptations))
        item["qa_policy"] = _POLICY
        changed = True

    if changed:
        path.write_text(
            json.dumps(segments, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log(
            f"semantic rescue round {rescue_round}: IDs {sorted(failed)}; "
            "слова сохранены, reference переключён, tail_guard >= 240 ms"
        )


def _find_original_renderer(command: Sequence[str]) -> Path:
    for part in command:
        if Path(str(part)).name.casefold() == base._SYNTH_NAME.casefold():
            path = Path(str(part)).resolve()
            if path.is_file():
                return path
    raise RuntimeError(
        "Не найден исходный NoChew renderer для semantic rescue."
    )


def _resume_seed(work_dir: Path, marker: dict[str, Any], command: Sequence[str]) -> int:
    checkpoint_seed = int(focused._checkpoint_base_seed(work_dir) or 0)
    if checkpoint_seed > 0:
        return checkpoint_seed
    try:
        marker_seed = int(marker.get("base_seed") or 0)
    except (TypeError, ValueError):
        marker_seed = 0
    if marker_seed > 0:
        return marker_seed
    return int(legacy._flag_value(command, "--base-seed")) + 500_000


def _run_prompt_rescue(
    command: Sequence[str],
    *args: Any,
    **kwargs: Any,
) -> Any:
    original_command = [str(part) for part in command]
    work_dir = Path(
        legacy._flag_value(original_command, "--work-dir")
    ).resolve()
    timeline = Path(
        legacy._flag_value(original_command, "--output")
    ).resolve()
    source_segments = Path(
        legacy._flag_value(original_command, "--segments-json")
    ).resolve()
    marker = _load_json(work_dir / "semantic_guard.marker.json")
    report = _load_json(timeline.with_suffix(".semantic_qa.json"))
    failed_ids = _rescue_ids(report, marker)
    if not failed_ids:
        raise RuntimeError(
            "Semantic rescue вызван без подтверждённого persistent QA failure."
        )

    guard_dir = work_dir / "semantic_guard_v4"
    guard_dir.mkdir(parents=True, exist_ok=True)
    guarded_segments = guard_dir / "segments_guarded.json"
    segments = _load_segments(guarded_segments)
    if not segments:
        segments = legacy._prepare_guarded_segments(
            source_segments,
            guarded_segments,
        )
    all_ids = {int(item["id"]) for item in segments}

    professional_renderer = (
        Path(base.__file__).resolve().parent / base._QUALITY_RENDERER
    )
    rescue_renderer = (
        Path(__file__).resolve().parent / "voxcpm2_semantic_rescue_v47.py"
    )
    original_renderer = _find_original_renderer(original_command)
    if not professional_renderer.is_file():
        raise RuntimeError(
            f"Не найден Professional renderer: {professional_renderer}"
        )
    if not rescue_renderer.is_file():
        raise RuntimeError(
            f"Не найден semantic rescue renderer: {rescue_renderer}"
        )

    prompt_path = _prompt_texts(original_command, guard_dir)
    rewritten = list(original_command)
    for index, part in enumerate(rewritten):
        if Path(part).name.casefold() == base._SYNTH_NAME.casefold():
            rewritten[index] = str(rescue_renderer)
            break
    legacy._replace_flag(
        rewritten,
        "--segments-json",
        str(guarded_segments),
    )

    env = dict(kwargs.get("env") or os.environ)
    env["VOXCPM_ORIGINAL_RENDERER"] = str(original_renderer)
    env["VOXCPM_RESCUE_RENDERER"] = str(professional_renderer)
    env["VOXCPM_PROMPT_TEXTS_JSON"] = str(prompt_path)
    env["VOXCPM_SEMANTIC_GUARD_VERSION"] = base._GUARD_VERSION
    kwargs["env"] = env

    resume_seed = _resume_seed(work_dir, marker, original_command)
    last_report = report
    last_report_path = timeline.with_suffix(".semantic_qa.json")
    for rescue_round in range(1, _RESCUE_ROUNDS + 1):
        _adapt_rescue_segments(
            guarded_segments,
            segments,
            failed_ids,
            rescue_round=rescue_round,
        )
        round_seed = resume_seed + (rescue_round - 1) * 100_000
        legacy._replace_flag(
            rewritten,
            "--base-seed",
            str(round_seed),
        )
        env["VOXCPM_RESCUE_CFG"] = (
            "1.95" if rescue_round == 1 else "2.05"
        )
        kwargs["env"] = env

        log(
            f"semantic rescue {rescue_round}/{_RESCUE_ROUNDS}; "
            f"только IDs {sorted(failed_ids)}; seed={round_seed}"
        )
        result = base._REAL_SUBPROCESS.run(
            rewritten,
            *args,
            **kwargs,
        )
        if int(getattr(result, "returncode", 1)) != 0:
            return result

        last_report_path = timeline.with_suffix(
            f".semantic_qa_v47.rescue{rescue_round}.json"
        )
        failed, last_report = base.verify_timeline_v4(
            timeline,
            segments,
            last_report_path,
        )
        if not failed:
            focused._write_marker(
                work_dir,
                state="complete_semantic_rescue",
                base_seed=round_seed,
                failed_ids=[],
                pipeline_signature=str(
                    marker.get("pipeline_signature") or ""
                ),
                report_path=last_report_path,
                round_index=(
                    int(marker.get("completed_rounds") or 5)
                    + rescue_round
                ),
            )
            timeline.with_suffix(".semantic_qa.json").write_text(
                json.dumps(last_report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log(
                "foreign prompt-leak устранён; все реплики прошли строгий QA"
            )
            return result

        failed_ids = {int(value) for value in failed}
        next_seed = round_seed + 100_000
        base._retarget(
            work_dir,
            good_ids=all_ids - failed_ids,
            failed_ids=failed_ids,
            new_base_seed=next_seed,
        )
        focused._write_marker(
            work_dir,
            state="partial_semantic_rescue",
            base_seed=next_seed,
            failed_ids=failed_ids,
            pipeline_signature=str(
                marker.get("pipeline_signature") or ""
            ),
            report_path=last_report_path,
            round_index=(
                int(marker.get("completed_rounds") or 5)
                + rescue_round
            ),
        )

    final_report = timeline.with_suffix(".semantic_qa.json")
    final_report.write_text(
        json.dumps(last_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    details = focused._failure_summary(last_report)
    raise RuntimeError(
        "Semantic prompt rescue не принял устойчивую русскую реплику после "
        f"{_RESCUE_ROUNDS} специальных раундов. "
        f"Сегменты: {sorted(failed_ids)}. "
        f"Причины: {details}. Отчёт: {final_report}"
    )


def _run_quality_synth_v47(
    command: Sequence[str],
    *args: Any,
    **kwargs: Any,
) -> Any:
    original_command = [str(part) for part in command]
    work_dir = Path(
        legacy._flag_value(original_command, "--work-dir")
    ).resolve()
    timeline = Path(
        legacy._flag_value(original_command, "--output")
    ).resolve()
    marker = _load_json(work_dir / "semantic_guard.marker.json")
    report = _load_json(timeline.with_suffix(".semantic_qa.json"))
    try:
        completed = int(marker.get("completed_rounds") or 0)
    except (TypeError, ValueError):
        completed = 0

    state = str(marker.get("state") or "")
    if (
        (completed >= 5 and _prompt_leak_ids(report))
        or state.startswith("partial_semantic_rescue")
    ):
        log(
            "обычные focused rounds уже исчерпаны; "
            "сразу запускаю semantic prompt rescue"
        )
        return _run_prompt_rescue(command, *args, **kwargs)

    try:
        return _ORIGINAL_FOCUSED_RUN(command, *args, **kwargs)
    except RuntimeError:
        report = _load_json(timeline.with_suffix(".semantic_qa.json"))
        marker = _load_json(work_dir / "semantic_guard.marker.json")
        if _rescue_ids(report, marker):
            log("подтверждён persistent foreign prompt-leak после focused QA")
            return _run_prompt_rescue(command, *args, **kwargs)
        raise


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    focused.install()
    base._run_quality_synth = _run_quality_synth_v47
    _INSTALLED = True
    log(
        "installed direct resume into exact prompt-transcript rescue for "
        "persistent foreign-language segments"
    )


__all__ = [
    "_adapt_rescue_segments",
    "_prompt_leak_ids",
    "_rescue_ids",
    "_resume_seed",
    "_run_quality_synth_v47",
    "install",
]
