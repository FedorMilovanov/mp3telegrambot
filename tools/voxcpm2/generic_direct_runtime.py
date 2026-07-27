#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Direct ready-SRT -> Russian VoxCPM2 production runtime.

This mode treats the uploaded Russian SRT as final editorial copy. Gemini is
used only for a lightweight Russian output filename; it never reviews,
rewrites, shortens, or translates the user's text.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

from tools.voxcpm2 import generic_short_production as pipeline
from tools.voxcpm2 import generic_short_runtime as hardened
from tools.voxcpm2.generic_project_runtime import (
    _hardlink_or_copy,
    _run_voxcpm_and_master,
    _telegram_entry,
    current_project_id,
    generate_russian_title,
    load_request,
    project_root,
    save_json,
    update_project_record,
)

_TAG_RE = re.compile(r"<[^>]+>")
_TIMING_RE = re.compile(
    r"^\s*(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})(?:\s+.*)?$"
)


def log(message: str) -> None:
    pipeline.log(message)


def _spoken_text(lines: Iterable[str]) -> str:
    """Strip SRT formatting only; preserve the user's words and punctuation."""
    value = " ".join(str(line).strip() for line in lines if str(line).strip())
    value = html.unescape(_TAG_RE.sub("", value))
    value = value.replace(r"\N", " ").replace(r"\n", " ")
    return re.sub(r"\s+", " ", value).strip()


def parse_srt_text(text: str) -> list[pipeline.Cue]:
    """Parse ordinary SRT without changing the Russian copy."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if not normalized.strip():
        raise RuntimeError("SRT пуст.")

    blocks = re.split(r"\n[ \t]*\n+", normalized.strip())
    cues: list[pipeline.Cue] = []
    for block_number, block in enumerate(blocks, start=1):
        lines = [line.rstrip() for line in block.splitlines()]
        if not lines:
            continue

        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), -1)
        if timing_index < 0:
            raise RuntimeError(f"В блоке SRT #{block_number} нет строки таймкодов.")

        match = _TIMING_RE.match(lines[timing_index])
        if not match:
            raise RuntimeError(f"Некорректные таймкоды в блоке SRT #{block_number}.")
        start = pipeline.parse_timestamp(match.group("start"))
        end = pipeline.parse_timestamp(match.group("end"))
        if end <= start:
            raise RuntimeError(f"В блоке SRT #{block_number} окончание не позже начала.")

        spoken = _spoken_text(lines[timing_index + 1 :])
        if not spoken:
            raise RuntimeError(f"В блоке SRT #{block_number} нет русского текста.")
        cues.append(pipeline.Cue(float(start), float(end), spoken))

    if not cues:
        raise RuntimeError("В SRT не найдено ни одной реплики.")
    return cues


def normalize_srt_cues(
    cues: list[pipeline.Cue],
    duration: float,
) -> tuple[list[pipeline.Cue], list[str]]:
    """Make timing technically renderable without touching wording."""
    if duration <= 0:
        raise RuntimeError("Не удалось определить длительность видео.")

    ordered = sorted(enumerate(cues), key=lambda item: (item[1].start, item[0]))
    result: list[pipeline.Cue] = []
    adjustments: list[str] = []

    for original_index, cue in ordered:
        number = original_index + 1
        start = max(0.0, float(cue.start))
        end = min(float(duration), float(cue.end))
        text = str(cue.text).strip()

        if start >= duration:
            raise RuntimeError(
                f"Реплика SRT #{number} начинается после конца видео "
                f"({start:.3f} >= {duration:.3f})."
            )
        if end <= start:
            end = min(duration, start + 0.35)
            adjustments.append(f"#{number}: слишком короткий интервал расширен технически.")

        if result and start < result[-1].end:
            previous = result[-1]
            overlap = previous.end - start
            combined = previous.text if text == previous.text else f"{previous.text} {text}".strip()
            if end <= previous.end + 0.40 or overlap >= 0.20:
                result[-1] = pipeline.Cue(previous.start, max(previous.end, end), combined)
                adjustments.append(f"#{number}: пересечение объединено с предыдущей репликой.")
                continue
            start = previous.end
            adjustments.append(f"#{number}: начало сдвинуто после предыдущей реплики.")

        if end - start < 0.35:
            if result and start - result[-1].end <= 0.45:
                previous = result[-1]
                combined = previous.text if text == previous.text else f"{previous.text} {text}".strip()
                result[-1] = pipeline.Cue(previous.start, max(previous.end, end), combined)
                adjustments.append(f"#{number}: короткая реплика объединена с предыдущей.")
                continue
            end = min(duration, start + 0.35)
            adjustments.append(f"#{number}: минимальное окно увеличено до 0.35 сек.")

        result.append(pipeline.Cue(start, end, text))

    if not result:
        raise RuntimeError("После технической нормализации SRT не осталось реплик.")
    return result, adjustments


def group_srt_cues(
    cues: list[pipeline.Cue],
    *,
    target_seconds: float = 7.5,
    max_seconds: float = 12.5,
) -> list[dict[str, Any]]:
    """Join subtitle-sized cues into natural TTS phrases, preserving all words."""
    groups: list[dict[str, Any]] = []
    current: list[pipeline.Cue] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        groups.append(
            {
                "id": len(groups) + 1,
                "start": float(current[0].start),
                "end": float(current[-1].end),
                "source": " ".join(item.text for item in current).strip(),
            }
        )
        current = []

    for cue in cues:
        if not current:
            current = [cue]
            continue

        gap = max(0.0, cue.start - current[-1].end)
        prospective = cue.end - current[0].start
        current_duration = current[-1].end - current[0].start
        sentence_end = bool(re.search(r"[.!?…][\"')\]]?$", current[-1].text.rstrip()))

        if (
            gap > 1.15
            or prospective > max_seconds
            or (sentence_end and current_duration >= target_seconds * 0.68)
        ):
            flush()
            current = [cue]
        else:
            current.append(cue)

    flush()

    if len(groups) >= 2:
        tail = groups[-1]
        previous = groups[-2]
        if (
            tail["end"] - tail["start"] < 2.2
            and tail["end"] - previous["start"] <= max_seconds * 1.20
        ):
            groups[-2] = {
                "id": previous["id"],
                "start": previous["start"],
                "end": tail["end"],
                "source": f"{previous['source']} {tail['source']}".strip(),
            }
            groups.pop()

    for index, group in enumerate(groups, start=1):
        group["id"] = index
    return groups


def _build_direct_segments(
    groups: list[dict[str, Any]],
    *,
    delay_ms: int,
    duration: float,
) -> tuple[list[dict[str, Any]], list[pipeline.Cue]]:
    delay = max(0, int(delay_ms)) / 1000.0
    segments: list[dict[str, Any]] = []
    subtitles: list[pipeline.Cue] = []

    for index, group in enumerate(groups, start=1):
        start = max(0.0, float(group["start"]))
        source_end = min(float(duration), float(group["end"]))
        max_delay = max(0.0, duration - start - 0.35)
        effective_delay = min(delay, max_delay)
        effective_delay_ms = int(round(effective_delay * 1000.0))
        latest_render_end = max(start + 0.35, duration - effective_delay)
        render_end = max(start + 0.35, source_end - effective_delay)
        render_end = min(render_end, latest_render_end)
        if render_end <= start:
            raise RuntimeError(f"Реплика #{index} не помещается до конца видео.")
        profile = "composite" if index == len(groups) or index % 4 == 0 else "extended"
        text = str(group["source"]).strip()

        segments.append(
            {
                "id": index,
                "start": round(start, 3),
                "end": round(render_end, 3),
                "start_delay_ms": effective_delay_ms,
                "reference_profile": profile,
                "tail_guard": 0.36 if profile == "extended" else 0.42,
                "text": text,
                "source_end": round(source_end, 3),
                "source": text,
            }
        )
        subtitle_start = min(duration, start + effective_delay)
        subtitle_end = max(subtitle_start + 0.05, source_end)
        subtitles.append(pipeline.Cue(subtitle_start, min(duration, subtitle_end), text))

    return segments, subtitles


def _write_plain_translation(groups: list[dict[str, Any]], path: Path) -> None:
    path.write_text(
        "\n\n".join(f"[{item['id']}] {item['source']}" for item in groups).rstrip() + "\n",
        encoding="utf-8",
    )


def _require_output(path: Path, label: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"Не создан обязательный результат: {label} ({path}).")


def main() -> None:
    pipeline.configure_utf8()
    parser = argparse.ArgumentParser(description="Direct ready-SRT Dub Studio runtime")
    parser.add_argument("-Mode", "--mode", choices=("direct",), default="direct")
    parser.parse_args()

    hardened.install_runtime_adapters()
    project_id = current_project_id()
    root = project_root(project_id)
    request = load_request(root)
    if str(request.get("translation_mode") or "") != "direct":
        raise RuntimeError("Этот runner предназначен только для режима готового SRT.")

    source_dir = root / "source"
    input_dir = root / "input"
    output_dir = root / "output"
    for directory in (source_dir, input_dir, output_dir):
        directory.mkdir(parents=True, exist_ok=True)

    uploaded_srt = input_dir / "ready_translation.srt"
    if not uploaded_srt.is_file():
        raise RuntimeError("Не найден готовый русский SRT. Пришлите его боту заново.")

    log("=== 1. ГОТОВЫЙ РУССКИЙ SRT ===")
    raw_srt = uploaded_srt.read_text(encoding="utf-8-sig")
    parsed_cues = parse_srt_text(raw_srt)

    log("=== 2. SOURCE / YOUTUBE ===")
    source = source_dir / "source.mp4"
    source_url = str(request["source_url"])
    video_id = str(request["video_id"])
    metadata = hardened.download_source(source_url, source)
    duration = pipeline.ffprobe_duration(source)
    log(f"Источник: {metadata.get('title') or video_id}")
    log(f"Длительность: {duration:.3f} сек.")

    normalized_cues, timing_adjustments = normalize_srt_cues(parsed_cues, duration)
    groups = group_srt_cues(normalized_cues)
    if not groups:
        raise RuntimeError("Не удалось построить речевые блоки из SRT.")

    log("=== 3. РУССКОЕ НАЗВАНИЕ ФАЙЛА ===")
    title_model = str(request.get("title_model") or "gemini-3.5-flash-lite")
    russian_title = generate_russian_title(
        metadata,
        groups,
        model_name=title_model,
        video_id=video_id,
    )
    (root / "russian_title.txt").write_text(russian_title + "\n", encoding="utf-8")
    update_project_record(
        project_id,
        title=russian_title,
        source_url=source_url,
        work_root=root,
        metadata={
            "video_id": video_id,
            "translation_mode": "direct",
            "russian_title": russian_title,
            "translation_source": "user_ready_srt",
            "srt_cues": len(parsed_cues),
            "render_groups": len(groups),
        },
    )

    delay_ms = int(request.get("russian_delay_ms") or 420)
    render_segments, shifted_cues = _build_direct_segments(
        groups,
        delay_ms=delay_ms,
        duration=duration,
    )
    segments_json = root / "segments_ru_final.json"
    final_srt = output_dir / "russian_subtitles.srt"
    translation_txt = output_dir / "russian_translation.txt"
    uploaded_copy = output_dir / "ready_translation_original.srt"
    save_json(segments_json, render_segments)
    pipeline.write_srt(shifted_cues, final_srt)
    _write_plain_translation(groups, translation_txt)
    shutil.copy2(uploaded_srt, uploaded_copy)

    stable_mixed = output_dir / "final_upload.mp4"
    stable_russian = output_dir / "russian_only.mp4"
    log("=== 4. VOXCPM2 / ГОТОВЫЙ ТЕКСТ БЕЗ GEMINI-ПРАВОК ===")
    russian_timeline = _run_voxcpm_and_master(
        root=root,
        request=request,
        source=source,
        cues=normalized_cues,
        duration=duration,
        segments_json=segments_json,
        final_mixed=stable_mixed,
        final_russian=stable_russian,
    )
    _require_output(stable_mixed, "главный MP4")
    _require_output(stable_russian, "версия только с русским голосом")
    _require_output(russian_timeline, "русская WAV-дорожка")

    named_mixed = output_dir / f"{russian_title} — русский дубляж.mp4"
    named_russian = output_dir / f"{russian_title} — только русский голос.mp4"
    named_srt = output_dir / f"{russian_title} — русские субтитры.srt"
    named_translation = output_dir / f"{russian_title} — готовый перевод.txt"
    named_original_srt = output_dir / f"{russian_title} — исходный готовый перевод.srt"
    _hardlink_or_copy(stable_mixed, named_mixed)
    _hardlink_or_copy(stable_russian, named_russian)
    _hardlink_or_copy(final_srt, named_srt)
    _hardlink_or_copy(translation_txt, named_translation)
    _hardlink_or_copy(uploaded_copy, named_original_srt)

    qa_path = output_dir / "translation_qa.txt"
    qa_lines = [
        "Режим: пользовательский готовый SRT.",
        "Gemini не переводил, не проверял, не сокращал и не переписывал русский текст.",
        f"Исходных SRT-реплик: {len(parsed_cues)}.",
        f"Речевых блоков VoxCPM2: {len(render_segments)}.",
        f"Технических корректировок таймкодов: {len(timing_adjustments)}.",
    ]
    if timing_adjustments:
        qa_lines.extend(["", *timing_adjustments])
    qa_path.write_text("\n".join(qa_lines).rstrip() + "\n", encoding="utf-8")

    manifest = {
        "schema_version": 3,
        "phase": "completed",
        "project_id": project_id,
        "video_id": video_id,
        "source_url": source_url,
        "original_title": metadata.get("title") or "",
        "russian_title": russian_title,
        "channel": metadata.get("uploader") or metadata.get("channel") or "",
        "duration_seconds": round(duration, 4),
        "translation_mode": "direct",
        "translation_model": "user",
        "translation_passes": 0,
        "translation_rewritten": false,
        "original_level": float(request.get("original_level") or 0.18),
        "russian_delay_ms": delay_ms,
        "srt_cues": len(parsed_cues),
        "render_segments": len(render_segments),
        "timing_adjustments": timing_adjustments,
        "outputs": {
            "mixed": str(named_mixed),
            "russian_only": str(named_russian),
            "russian_srt": str(named_srt),
            "uploaded_srt": str(named_original_srt),
            "translation": str(named_translation),
            "qa": str(qa_path),
            "russian_timeline": str(russian_timeline),
        },
        "telegram_outputs": [
            _telegram_entry(named_mixed, filename=named_mixed.name, label="Готовый ролик: ваш SRT без изменений, оригинал 18%", primary=True, video=True),
            _telegram_entry(named_russian, filename=named_russian.name, label="Версия только с русским голосом", video=True, send_default=False),
            _telegram_entry(named_srt, filename=named_srt.name, label="Финальные русские субтитры с задержкой 420 мс"),
            _telegram_entry(named_original_srt, filename=named_original_srt.name, label="Ваш исходный готовый SRT"),
            _telegram_entry(named_translation, filename=named_translation.name, label="Ваш русский текст по речевым блокам"),
            _telegram_entry(qa_path, filename=f"{russian_title} — технический отчёт.txt", label="Технический отчёт без проверки перевода"),
        ],
    }
    save_json(output_dir / "manifest.json", manifest)
    log("=== ГОТОВО ===")
    log(f"Mixed: {named_mixed}")
    log(f"Russian-only: {named_russian}")


if __name__ == "__main__":
    main()
