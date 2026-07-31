#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-action YouTube Shorts -> Russian speech-backend production pipeline.

The bot process only queues this module. This process downloads the source,
extracts an English transcript, performs a two-pass literal-literary Russian
translation, prepares voice references, renders the selected speech backend on
its declared runtime, and masters mixed/Russian-only upload-ready videos.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from services.speech_backends import DEFAULT_BACKEND_ID, get_backend


def configure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def log(message: str) -> None:
    print(message, flush=True)


def run_checked(command: list[str], *, capture: bool = False, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        command,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        tail = ((proc.stderr or "") + "\n" + (proc.stdout or ""))[-7000:] if capture else ""
        raise RuntimeError("Команда завершилась с ошибкой:\n" + " ".join(command) + ("\n\n" + tail if tail else ""))
    return proc


def require_tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"{name} не найден в PATH.")
    return found


def ffprobe_duration(path: Path) -> float:
    proc = run_checked(
        [
            require_tool("ffprobe"),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture=True,
        timeout=60,
    )
    value = float((proc.stdout or "0").strip())
    if value <= 0:
        raise RuntimeError(f"Нулевая длительность: {path}")
    return value


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


_TAG_RE = re.compile(r"<[^>]+>")


def parse_timestamp(value: str) -> float:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    raise ValueError(value)


def clean_caption_text(value: str) -> str:
    value = html.unescape(_TAG_RE.sub("", str(value or "")))
    value = value.replace("&nbsp;", " ")
    value = re.sub(r"\[[^\]]{1,40}\]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def parse_vtt(path: Path) -> list[Cue]:
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    raw: list[Cue] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue
        left, right = line.split("-->", 1)
        right = right.strip().split()[0]
        try:
            start = parse_timestamp(left)
            end = parse_timestamp(right)
        except ValueError:
            index += 1
            continue
        index += 1
        payload: list[str] = []
        while index < len(lines) and lines[index].strip():
            payload.append(lines[index].strip())
            index += 1
        cleaned_lines = [clean_caption_text(item) for item in payload]
        cleaned_lines = [item for item in cleaned_lines if item]
        if cleaned_lines:
            # Auto-captions often repeat the previous rolling line; the last line
            # contains the newest complete text for this cue.
            text = cleaned_lines[-1]
            if end > start and text:
                raw.append(Cue(start, end, text))
        index += 1

    result: list[Cue] = []
    for cue in raw:
        if result and cue.text.casefold() == result[-1].text.casefold():
            previous = result[-1]
            result[-1] = Cue(previous.start, max(previous.end, cue.end), previous.text)
            continue
        if result and cue.start < result[-1].end and cue.text.casefold().startswith(result[-1].text.casefold()):
            result[-1] = Cue(result[-1].start, cue.end, cue.text)
            continue
        result.append(cue)
    return result


def write_srt(cues: Iterable[Cue], path: Path) -> None:
    def stamp(value: float) -> str:
        total_ms = max(0, int(round(value * 1000)))
        hours, rem = divmod(total_ms, 3_600_000)
        minutes, rem = divmod(rem, 60_000)
        seconds, millis = divmod(rem, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    blocks = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(f"{index}\n{stamp(cue.start)} --> {stamp(cue.end)}\n{cue.text}")
    path.write_text("\n\n".join(blocks).rstrip() + "\n", encoding="utf-8")


def normalize_cues(cues: list[Cue], duration: float) -> list[Cue]:
    cleaned: list[Cue] = []
    for cue in cues:
        start = max(0.0, min(float(cue.start), duration))
        end = max(start + 0.05, min(float(cue.end), duration))
        text = clean_caption_text(cue.text)
        if not text:
            continue
        if cleaned and start < cleaned[-1].end:
            start = cleaned[-1].end
        if end <= start:
            continue
        cleaned.append(Cue(start, end, text))
    if not cleaned:
        raise RuntimeError("Английская речь не распознана.")
    return cleaned


def group_cues(cues: list[Cue], *, target_seconds: float = 9.0, max_seconds: float = 13.5) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    current: list[Cue] = []
    for cue in cues:
        if not current:
            current = [cue]
            continue
        prospective = cue.end - current[0].start
        previous_text = current[-1].text.rstrip()
        sentence_end = bool(re.search(r"[.!?][\"')\]]?$", previous_text))
        if prospective > max_seconds or (sentence_end and current[-1].end - current[0].start >= target_seconds * 0.68):
            groups.append({
                "id": len(groups) + 1,
                "start": current[0].start,
                "end": current[-1].end,
                "english": " ".join(item.text for item in current),
            })
            current = [cue]
        else:
            current.append(cue)
    if current:
        groups.append({
            "id": len(groups) + 1,
            "start": current[0].start,
            "end": current[-1].end,
            "english": " ".join(item.text for item in current),
        })

    if len(groups) >= 2 and groups[-1]["end"] - groups[-1]["start"] < 3.2:
        tail = groups.pop()
        groups[-1]["end"] = tail["end"]
        groups[-1]["english"] = (groups[-1]["english"] + " " + tail["english"]).strip()
    for index, group in enumerate(groups, start=1):
        group["id"] = index
    return groups


def _extract_json(text: str) -> Any:
    text = str(text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def gemini_json(prompt: str, *, model_name: str) -> Any:
    api_key = next(
        (os.getenv(name, "").strip() for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4") if os.getenv(name, "").strip()),
        "",
    )
    if not api_key:
        raise RuntimeError("Для редакторского перевода нужен GEMINI_API_KEY в .env.")
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("Пакет google-genai не установлен в окружении бота.") from exc

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        max_output_tokens=16000,
    )
    response = client.models.generate_content(model=model_name, contents=prompt, config=config)
    return _extract_json(getattr(response, "text", ""))


def validate_translation(payload: Any, source_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("segments")
    if not isinstance(payload, list):
        raise RuntimeError("Переводчик не вернул список segments.")
    by_id: dict[int, dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            item_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        text = re.sub(r"\s+", " ", str(item.get("russian") or item.get("text") or "")).strip()
        if text:
            by_id[item_id] = {"id": item_id, "russian": text}
    expected = [int(group["id"]) for group in source_groups]
    if sorted(by_id) != expected:
        raise RuntimeError(f"Нарушены ID перевода: ожидались {expected}, получены {sorted(by_id)}")
    return [by_id[item_id] for item_id in expected]


def translate_groups(groups: list[dict[str, Any]], *, model_name: str) -> list[dict[str, Any]]:
    source_json = json.dumps(groups, ensure_ascii=False, indent=2)
    first_prompt = f"""
Ты — русский литературный редактор и переводчик богословской устной речи для профессионального закадрового дубляжа.

Сделай буквально-литературный перевод каждого английского блока на естественный русский язык.

Непреложные правила:
1. Сохрани ВСЮ мысль, логические связи, отрицания, числа, имена, библейские ссылки и риторическую силу.
2. Ничего не добавляй от себя, не объясняй и не пересказывай.
3. Не калькируй английский синтаксис: по-русски фраза должна звучать как живая сильная речь.
4. Не смягчай резкие формулировки автора и не усиливай их сверх оригинала.
5. Сохрани ID один к одному. Не объединяй и не дроби блоки.
6. Перевод предназначен для произнесения в указанное временное окно. Будь ёмким, но не жертвуй смыслом.
7. Используй нормативную русскую библейскую и богословскую лексику только там, где она действительно есть в оригинале.

Верни только JSON вида:
{{"segments":[{{"id":1,"russian":"..."}}]}}

Исходные блоки:
{source_json}
""".strip()
    draft = validate_translation(gemini_json(first_prompt, model_name=model_name), groups)

    review_prompt = f"""
Ты — старший редактор русского дубляжа. Сверь черновик с английским оригиналом построчно и верни окончательную редакцию.

Исправляй только реальные недостатки: потерю смысла, добавление, неправильное отрицание, неточный термин, буквальную кальку, неестественный русский порядок слов или чрезмерную длину. Сохрани авторский тон. Не украшай и не богословствуй сверх исходника.

ID должны остаться прежними и идти один к одному. Верни только JSON:
{{"segments":[{{"id":1,"russian":"..."}}]}}

ОРИГИНАЛ:
{source_json}

ЧЕРНОВИК:
{json.dumps(draft, ensure_ascii=False, indent=2)}
""".strip()
    final = validate_translation(gemini_json(review_prompt, model_name=model_name), groups)

    overloaded: list[dict[str, Any]] = []
    for source, translated in zip(groups, final, strict=True):
        available = max(1.0, float(source["end"]) - float(source["start"]))
        words_per_second = len(translated["russian"].split()) / available
        if words_per_second > 3.25:
            overloaded.append({
                "id": source["id"],
                "seconds": round(available, 3),
                "english": source["english"],
                "russian": translated["russian"],
            })
    if overloaded:
        compression_prompt = f"""
Сократи только перечисленные русские реплики до естественной произносимой длины, не теряя ни одного утверждения, отрицания, имени, числа, причины или вывода. Не превращай перевод в конспект. Убирай лишь словесную избыточность и кальки.

Верни JSON {{"segments":[{{"id":1,"russian":"..."}}]}} только для указанных ID.

Реплики:
{json.dumps(overloaded, ensure_ascii=False, indent=2)}
""".strip()
        compact_payload = gemini_json(compression_prompt, model_name=model_name)
        compact_list = compact_payload.get("segments") if isinstance(compact_payload, dict) else compact_payload
        compact_by_id = {
            int(item["id"]): re.sub(r"\s+", " ", str(item.get("russian") or "")).strip()
            for item in compact_list or []
            if isinstance(item, dict) and str(item.get("id", "")).isdigit() and str(item.get("russian") or "").strip()
        }
        for item in final:
            if item["id"] in compact_by_id:
                item["russian"] = compact_by_id[item["id"]]
    return final


def transcript_hash(groups: list[dict[str, Any]]) -> str:
    payload = json.dumps(groups, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_reference(source: Path, intervals: list[tuple[float, float]], output: Path, *, target_seconds: float) -> None:
    if not intervals:
        raise RuntimeError("Не удалось выбрать интервалы для голосового референса.")
    ffmpeg = require_tool("ffmpeg")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dub-ref-") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        parts: list[Path] = []
        accumulated = 0.0
        for index, (start, end) in enumerate(intervals, start=1):
            if accumulated >= target_seconds:
                break
            duration = min(max(0.8, end - start), target_seconds - accumulated, 8.0)
            if duration < 0.75:
                continue
            part = temp_dir / f"part_{index:02d}.wav"
            run_checked([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{max(0.0, start - 0.08):.3f}",
                "-t", f"{duration:.3f}",
                "-i", str(source),
                "-vn", "-ac", "1", "-ar", "16000",
                "-af", "highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2",
                str(part),
            ], timeout=180)
            parts.append(part)
            accumulated += duration
        if not parts:
            raise RuntimeError("Голосовой референс оказался пустым.")
        concat_list = temp_dir / "concat.txt"
        concat_list.write_text("\n".join(f"file '{part.as_posix()}'" for part in parts) + "\n", encoding="utf-8")
        run_checked([
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-af", "loudnorm=I=-20:LRA=7:TP=-2",
            "-ac", "1", "-ar", "16000", str(output),
        ], timeout=240)


def reference_intervals(cues: list[Cue], duration: float) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    candidates = [(cue.start, cue.end) for cue in cues if cue.end - cue.start >= 0.75 and len(cue.text.split()) >= 3]
    if not candidates:
        candidates = [(0.2, min(duration, 24.2))]
    extended = candidates
    thirds: list[list[tuple[float, float]]] = [[], [], []]
    for interval in candidates:
        midpoint = (interval[0] + interval[1]) / 2
        bucket = min(2, int(midpoint / max(duration, 0.1) * 3))
        thirds[bucket].append(interval)
    composite: list[tuple[float, float]] = []
    for bucket in thirds:
        if bucket:
            composite.extend(sorted(bucket, key=lambda pair: pair[1] - pair[0], reverse=True)[:2])
    return extended, sorted(composite or candidates, key=lambda pair: pair[0])


def download_source(url: str, source: Path) -> dict[str, Any]:
    source.parent.mkdir(parents=True, exist_ok=True)
    metadata_proc = run_checked(
        [sys.executable, "-m", "yt_dlp", "--dump-single-json", "--skip-download", "--no-playlist", url],
        capture=True,
        timeout=180,
    )
    metadata = json.loads(metadata_proc.stdout or "{}")
    if not source.is_file() or source.stat().st_size < 100_000:
        run_checked([
            sys.executable, "-m", "yt_dlp",
            "--no-playlist", "--windows-filenames",
            "-f", "bv*+ba/b", "--merge-output-format", "mp4",
            "-o", str(source), url,
        ], timeout=1800)
    return metadata


def download_captions(url: str, source_dir: Path) -> list[Cue]:
    template = source_dir / "captions.%(language)s.%(ext)s"
    proc = subprocess.run(
        [
            sys.executable, "-m", "yt_dlp", "--no-playlist", "--skip-download",
            "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en",
            "--sub-format", "vtt", "-o", str(template), url,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    files = sorted(source_dir.glob("captions*.vtt"), key=lambda p: p.stat().st_size, reverse=True)
    for path in files:
        cues = parse_vtt(path)
        if cues:
            log(f"Использую YouTube captions: {path.name}")
            return cues
    if proc.returncode != 0:
        log("YouTube captions недоступны; включаю Whisper fallback.")
    return []


def whisper_transcribe(source: Path, *, model_name: str) -> list[Cue]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Нет captions и не установлен faster-whisper.") from exc
    log(f"Whisper fallback: {model_name}, CPU/int8...")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(source),
        language="en",
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=True,
        word_timestamps=False,
    )
    cues = [Cue(float(seg.start), float(seg.end), clean_caption_text(seg.text)) for seg in segments if clean_caption_text(seg.text)]
    log(f"Whisper language={getattr(info, 'language', 'en')}; segments={len(cues)}")
    return cues


def main() -> None:
    configure_utf8()
    parser = argparse.ArgumentParser(description="Generic premium speech-backend Shorts production")
    parser.add_argument("-SpeechBackend", "--speech-backend", dest="speech_backend", default="voxcpm2")
    parser.add_argument("-SourceUrl", "--source-url", dest="source_url", required=True)
    parser.add_argument("-VideoId", "--video-id", dest="video_id", required=True)
    parser.add_argument("-WorkRoot", "--work-root", dest="work_root", required=True)
    # Legacy flag names remain accepted, but defaults belong to the selected
    # backend adapter rather than this shared CLI.
    parser.add_argument("-VoxArchive", "--vox-archive", dest="vox_archive", default=None)
    parser.add_argument("-CpuVenv", "--cpu-venv", dest="cpu_venv", default=None)
    parser.add_argument("-OriginalLevel", "--original-level", dest="original_level", type=float, default=0.18)
    parser.add_argument("-RussianDelayMs", "--russian-delay-ms", dest="russian_delay_ms", type=int, default=420)
    parser.add_argument("-Threads", "--threads", dest="threads", type=int, default=10)
    parser.add_argument("-Steps", "--steps", dest="steps", type=int, default=16)
    parser.add_argument("-Cfg", "--cfg", dest="cfg", type=float, default=1.80)
    parser.add_argument("-WhisperModel", "--whisper-model", dest="whisper_model", default="large-v3")
    parser.add_argument("-TranslationModel", "--translation-model", dest="translation_model", default=os.getenv("DUB_TRANSLATION_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-3.5-flash")
    args = parser.parse_args()

    if not 0.0 <= args.original_level <= 1.0:
        raise RuntimeError("OriginalLevel должен быть 0..1.")
    if not 0 <= args.russian_delay_ms <= 1500:
        raise RuntimeError("RussianDelayMs должен быть 0..1500.")
    require_tool("ffmpeg")
    require_tool("ffprobe")

    root = Path(args.work_root).expanduser().resolve()
    source_dir = root / "source"
    reference_dir = root / "references"
    audio_dir = root / "audio"
    output_dir = root / "output"
    segment_work = root / "segment_work"
    master_work = root / "master_work"
    for directory in (source_dir, reference_dir, audio_dir, output_dir, segment_work, master_work):
        directory.mkdir(parents=True, exist_ok=True)

    source = source_dir / "source.mp4"
    source_srt = output_dir / f"{args.video_id}_Source_English.srt"
    russian_srt = output_dir / f"{args.video_id}_Russian_Dub_FINAL.srt"
    translation_txt = output_dir / f"{args.video_id}_Russian_Translation.txt"
    segments_json = root / "segments_ru_final.json"
    translation_state = root / "translation_state.json"
    extended_reference = reference_dir / "extended_reference.wav"
    composite_reference = reference_dir / "composite_reference.wav"
    russian_timeline = audio_dir / f"{args.video_id}_ru_timeline.wav"
    final_mixed = output_dir / f"{args.video_id}_Russian_Dub_FINAL_UPLOAD.mp4"
    final_ru = output_dir / f"{args.video_id}_Russian_Dub_FINAL_RUSSIAN_ONLY.mp4"
    manifest_path = output_dir / f"{args.video_id}_FINAL.manifest.json"

    log("=== 1. SOURCE / YOUTUBE ===")
    metadata = download_source(args.source_url, source)
    duration = ffprobe_duration(source)
    log(f"Источник: {metadata.get('title') or args.video_id}")
    log(f"Автор канала: {metadata.get('uploader') or metadata.get('channel') or 'не определён'}")
    log(f"Длительность: {duration:.3f} сек.")

    log("=== 2. ENGLISH TRANSCRIPT ===")
    cues = download_captions(args.source_url, source_dir)
    if not cues:
        cues = whisper_transcribe(source, model_name=args.whisper_model)
    cues = normalize_cues(cues, duration)
    write_srt(cues, source_srt)
    groups = group_cues(cues)
    log(f"Речь разбита на {len(groups)} смысловых блоков.")

    log("=== 3. LITERAL-LITERARY RUSSIAN TRANSLATION ===")
    signature = transcript_hash(groups)
    translations: list[dict[str, Any]]
    cached = None
    if translation_state.is_file():
        try:
            cached = json.loads(translation_state.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = None
    if isinstance(cached, dict) and cached.get("transcript_sha256") == signature and isinstance(cached.get("translations"), list):
        translations = validate_translation(cached["translations"], groups)
        log("Использую проверенный перевод из translation checkpoint.")
    else:
        translations = translate_groups(groups, model_name=args.translation_model)
        translation_state.write_text(
            json.dumps({
                "schema_version": 1,
                "transcript_sha256": signature,
                "translation_model": args.translation_model,
                "translations": translations,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    log("Двухпроходная редакторская сверка перевода завершена.")

    delay_sec = args.russian_delay_ms / 1000.0
    render_segments: list[dict[str, Any]] = []
    subtitle_cues: list[Cue] = []
    translation_lines: list[str] = []
    for index, (source_group, translated) in enumerate(zip(groups, translations, strict=True), start=1):
        source_start = float(source_group["start"])
        source_end = float(source_group["end"])
        render_end = max(source_start + 1.25, source_end - delay_sec)
        profile = "composite" if index == len(groups) or index % 4 == 0 else "extended"
        render_segments.append({
            "id": index,
            "start": round(source_start, 3),
            "end": round(render_end, 3),
            "start_delay_ms": int(args.russian_delay_ms),
            "reference_profile": profile,
            "tail_guard": 0.36 if profile == "extended" else 0.42,
            "text": translated["russian"],
            "source_end": round(source_end, 3),
            "english": source_group["english"],
        })
        subtitle_cues.append(Cue(min(duration, source_start + delay_sec), min(duration, source_end), translated["russian"]))
        translation_lines.append(f"{index}. {translated['russian']}")
    segments_json.write_text(json.dumps(render_segments, ensure_ascii=False, indent=2), encoding="utf-8")
    write_srt(subtitle_cues, russian_srt)
    translation_txt.write_text("\n\n".join(translation_lines).rstrip() + "\n", encoding="utf-8")

    log("=== 4. VOICE REFERENCES ===")
    extended_intervals, composite_intervals = reference_intervals(cues, duration)
    build_reference(source, extended_intervals, extended_reference, target_seconds=min(24.0, max(12.0, duration * 0.45)))
    build_reference(source, composite_intervals, composite_reference, target_seconds=min(21.0, max(10.0, duration * 0.38)))
    log("Extended и composite voice references готовы.")

    repo = Path(__file__).resolve().parents[2]
    backend = get_backend(args.speech_backend or DEFAULT_BACKEND_ID)
    runtime = backend.runtime_paths(
        repo,
        {
            "translation_mode": "direct",
            "speech_backend": backend.backend_id,
            "cpu_venv": args.cpu_venv,
            "vox_archive": args.vox_archive,
        },
    )
    cpu_python = runtime.cpu_python
    if not cpu_python.is_file():
        raise RuntimeError(
            f"CPU Python не найден для backend={backend.backend_id}: {cpu_python}"
        )
    env = backend.process_environment(
        {"threads": max(1, args.threads), "speech_backend": backend.backend_id},
        base_environment=os.environ,
    ).as_dict(os.environ)

    log(f"=== 5. {backend.backend_id} / NOCHEW ===")
    synth_command = backend.build_renderer_command(
        runtime,
        values={
            "extended_reference": str(extended_reference),
            "composite_reference": str(composite_reference),
            "segments_json": str(segments_json),
            "segment_work": str(segment_work),
            "timeline": str(russian_timeline),
            "threads": str(max(1, args.threads)),
            "steps": str(max(1, args.steps)),
            "cfg": str(args.cfg),
            "cache_length": "4096",
            "duration": f"{duration:.6f}",
            "base_seed": "2026072800",
        },
    )
    proc = subprocess.run(synth_command, cwd=str(repo), env=env, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Speech backend {backend.backend_id} CPU-синтез завершился с кодом {proc.returncode}."
        )

    log("=== 6. CONSTANT MIX / FINAL MASTER ===")
    log(f"Оригинал постоянно {args.original_level * 100:.1f}%; русский delay={args.russian_delay_ms} ms.")
    master_command = backend.build_master_command(
        runtime,
        values={
            "source": str(source),
            "timeline": str(russian_timeline),
            "master_work": str(master_work),
            "final_mixed": str(final_mixed),
            "final_russian": str(final_ru),
            "original_level": f"{args.original_level:.6f}",
            "target_i": "-14.0",
            "target_lra": "9.0",
            "target_tp": "-1.0",
        },
    )
    proc = subprocess.run(master_command, cwd=str(repo), env=env, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"Финальный master завершился с кодом {proc.returncode}.")

    manifest = {
        "schema_version": 1,
        "video_id": args.video_id,
        "source_url": args.source_url,
        "title": metadata.get("title") or "",
        "uploader": metadata.get("uploader") or metadata.get("channel") or "",
        "duration_seconds": round(duration, 4),
        "original_level": args.original_level,
        "russian_delay_ms": args.russian_delay_ms,
        "translation_model": args.translation_model,
        "translation_passes": 2,
        "segments": len(render_segments),
        "outputs": {
            "mixed": str(final_mixed),
            "russian_only": str(final_ru),
            "russian_srt": str(russian_srt),
            "english_srt": str(source_srt),
            "translation": str(translation_txt),
            "russian_timeline": str(russian_timeline),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    log("=== ГОТОВО ===")
    log(f"Mixed: {final_mixed}")
    log(f"Russian-only: {final_ru}")
    log(f"Russian subtitles: {russian_srt}")
    log(f"Translation: {translation_txt}")


if __name__ == "__main__":
    main()
