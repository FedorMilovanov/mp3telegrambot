#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Universal Dub Studio project runtime for arbitrary YouTube videos.

The Telegram wizard writes a trusted request.json under the project's private
runtime directory. This module reads that request, prefers creator-provided
YouTube subtitles, falls back to automatic captions and finally CPU Whisper,
then either performs a maximum-quality Gemini translation or renders a user
translation without rewriting it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from services.dub_studio import DubStore, studio_root, utc_now
from services.speech_backends import DEFAULT_BACKEND_ID, get_backend
from tools.voxcpm2 import generic_short_production as pipeline
from tools.voxcpm2 import generic_short_runtime as hardened

_PROJECT_RE = re.compile(r"^dub-[a-f0-9]{10}$")
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
_INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def log(message: str) -> None:
    pipeline.log(message)


def current_project_id() -> str:
    value = os.getenv("DUB_STUDIO_PROJECT_ID", "").strip().lower()
    if not _PROJECT_RE.fullmatch(value):
        raise RuntimeError("DUB_STUDIO_PROJECT_ID отсутствует или некорректен.")
    return value


def project_root(project_id: str | None = None) -> Path:
    project_id = project_id or current_project_id()
    root = (studio_root() / "projects" / project_id).resolve()
    allowed = (studio_root() / "projects").resolve()
    try:
        root.relative_to(allowed)
    except ValueError as exc:
        raise RuntimeError("Project root escaped Dub Studio projects directory.") from exc
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_request(root: Path) -> dict[str, Any]:
    path = root / "request.json"
    if not path.is_file():
        raise RuntimeError(f"Не найден request.json проекта: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or int(payload.get("schema_version", 0)) != 1:
        raise RuntimeError("Неподдерживаемый request.json проекта.")
    video_id = str(payload.get("video_id") or "").strip()
    source_url = str(payload.get("source_url") or "").strip()
    if not _VIDEO_ID_RE.fullmatch(video_id):
        raise RuntimeError("Некорректный video_id в request.json.")
    if not source_url.startswith(("https://youtube.com/", "https://www.youtube.com/", "https://youtu.be/", "https://m.youtube.com/")):
        raise RuntimeError("Generic Dub Studio принимает только YouTube URL.")
    return payload


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_russian_filename(value: str, fallback: str = "Русский дубляж", limit: int = 92) -> str:
    value = _INVALID_WINDOWS_CHARS.sub(" ", str(value or ""))
    value = re.sub(r"\s+", " ", value).strip(" .-")
    if not value:
        value = fallback
    if value.casefold() in _RESERVED_WINDOWS_NAMES:
        value = "Видео — " + value
    if len(value) > limit:
        value = value[:limit].rstrip(" .-")
    return value or fallback


def update_project_record(
    project_id: str,
    *,
    title: str,
    source_url: str,
    work_root: Path,
    metadata: dict[str, Any],
) -> None:
    store = DubStore()
    with store.connect() as conn:
        row = conn.execute("SELECT metadata_json FROM dub_projects WHERE id=?", (project_id,)).fetchone()
        current: dict[str, Any] = {}
        if row is not None:
            try:
                current = json.loads(str(row["metadata_json"] or "{}"))
            except (TypeError, json.JSONDecodeError):
                current = {}
        current.update(metadata)
        conn.execute(
            """
            UPDATE dub_projects
            SET title=?, source_url=?, work_root=?, metadata_json=?, updated_at=?
            WHERE id=?
            """,
            (
                title,
                source_url,
                str(work_root),
                json.dumps(current, ensure_ascii=False, separators=(",", ":")),
                utc_now(),
                project_id,
            ),
        )
        conn.commit()


def _language_priority(keys: Iterable[str], source_language: str = "") -> list[str]:
    items = [str(key) for key in keys if str(key) and str(key) != "live_chat"]
    seen: set[str] = set()
    result: list[str] = []

    def add(predicate: Any) -> None:
        for item in items:
            key = item.casefold()
            if key in seen or not predicate(key):
                continue
            seen.add(key)
            result.append(item)

    source = source_language.casefold().replace("_", "-").strip()
    if source:
        add(lambda key: key == source)
        add(lambda key: key.startswith(source + "-") or source.startswith(key + "-"))
    add(lambda key: key in {"en", "en-us", "en-gb", "en-orig"})
    add(lambda key: key.startswith("en-"))
    add(lambda key: key not in {"ru", "ru-ru"})
    add(lambda key: True)
    return result


def choose_caption_track(metadata: dict[str, Any]) -> tuple[str, str]:
    source_language = str(metadata.get("language") or metadata.get("original_language") or "")
    manual = metadata.get("subtitles") or {}
    automatic = metadata.get("automatic_captions") or {}
    if isinstance(manual, dict):
        ranked = _language_priority(manual.keys(), source_language)
        if ranked:
            return "manual", ranked[0]
    if isinstance(automatic, dict):
        ranked = _language_priority(automatic.keys(), source_language)
        if ranked:
            return "automatic", ranked[0]
    return "whisper", ""


def parse_manual_vtt(path: Path) -> list[pipeline.Cue]:
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    cues: list[pipeline.Cue] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if "-->" not in line:
            index += 1
            continue
        left, right = line.split("-->", 1)
        try:
            start = pipeline.parse_timestamp(left)
            end = pipeline.parse_timestamp(right.strip().split()[0])
        except ValueError:
            index += 1
            continue
        index += 1
        payload: list[str] = []
        while index < len(lines) and lines[index].strip():
            cleaned = pipeline.clean_caption_text(lines[index])
            if cleaned:
                payload.append(cleaned)
            index += 1
        text = " ".join(dict.fromkeys(payload)).strip()
        if text and end > start:
            cues.append(pipeline.Cue(start, end, text))
        index += 1
    return cues


def _download_track(url: str, source_dir: Path, *, kind: str, language: str) -> list[pipeline.Cue]:
    for old in source_dir.glob("preferred_caption*.vtt"):
        old.unlink(missing_ok=True)
    template = source_dir / "preferred_caption.%(language)s.%(ext)s"
    if kind == "manual":
        mode = ["--write-subs", "--no-write-auto-subs"]
    else:
        mode = ["--no-write-subs", "--write-auto-subs"]
    proc = subprocess.run(
        [
            *hardened._ytdlp_base(),
            "--no-playlist",
            "--skip-download",
            *mode,
            "--sub-langs",
            language,
            "--sub-format",
            "vtt",
            "-o",
            str(template),
            url,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=420,
        check=False,
    )
    files = sorted(source_dir.glob("preferred_caption*.vtt"), key=lambda path: path.stat().st_size, reverse=True)
    for path in files:
        cues = parse_manual_vtt(path) if kind == "manual" else pipeline.parse_vtt(path)
        if cues:
            log(f"Субтитры: {kind}, язык={language}, файл={path.name}")
            return cues
    if proc.returncode != 0:
        log(f"Не удалось получить {kind} captions ({language}); пробую следующий источник.")
    return []


def whisper_transcribe_auto(source: Path, *, model_name: str) -> tuple[list[pipeline.Cue], str]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Нет YouTube captions и не установлен faster-whisper.") from exc
    log(f"Whisper fallback: {model_name}, CPU/int8, автоопределение языка...")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(source),
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=True,
        word_timestamps=False,
    )
    cues = [
        pipeline.Cue(float(segment.start), float(segment.end), pipeline.clean_caption_text(segment.text))
        for segment in segments
        if pipeline.clean_caption_text(segment.text)
    ]
    language = str(getattr(info, "language", "") or "unknown")
    log(f"Whisper: язык={language}; сегментов={len(cues)}")
    return cues, language


def acquire_transcript(
    source_url: str,
    source: Path,
    source_dir: Path,
    metadata: dict[str, Any],
    *,
    whisper_model: str,
    duration: float,
) -> tuple[list[pipeline.Cue], str, str]:
    preferred_kind, preferred_language = choose_caption_track(metadata)
    cues: list[pipeline.Cue] = []
    used_kind = preferred_kind
    used_language = preferred_language
    if preferred_kind in {"manual", "automatic"}:
        cues = _download_track(source_url, source_dir, kind=preferred_kind, language=preferred_language)
    if not cues and preferred_kind == "manual":
        automatic = metadata.get("automatic_captions") or {}
        ranked = _language_priority(automatic.keys(), str(metadata.get("language") or "")) if isinstance(automatic, dict) else []
        if ranked:
            used_kind, used_language = "automatic", ranked[0]
            cues = _download_track(source_url, source_dir, kind="automatic", language=used_language)
    if not cues:
        cues, used_language = whisper_transcribe_auto(source, model_name=whisper_model)
        used_kind = "whisper"
    return pipeline.normalize_cues(cues, duration), used_kind, used_language


def _compact_context(groups: list[dict[str, Any]], limit: int = 7200) -> str:
    text = " ".join(str(group.get("source") or group.get("english") or "") for group in groups)
    return text[:limit]


def generate_russian_title(
    metadata: dict[str, Any],
    groups: list[dict[str, Any]],
    *,
    model_name: str,
    video_id: str,
) -> str:
    original_title = str(metadata.get("title") or "")
    channel = str(metadata.get("uploader") or metadata.get("channel") or "")
    prompt = f"""
Ты создаёшь имя готового русского видеофайла. Дай один естественный русский заголовок длиной 3–9 слов.
Передай центральную мысль ролика, без кликбейта, эмодзи, кавычек, двоеточия, номера выпуска и слов «русский дубляж».
Верни только JSON: {{"title":"..."}}

Исходное название: {original_title}
Канал/автор: {channel}
Фрагмент точной расшифровки: {_compact_context(groups, 2200)}
""".strip()
    try:
        payload = hardened.gemini_json(prompt, model_name=model_name)
        title = str(payload.get("title") if isinstance(payload, dict) else "")
    except Exception as exc:
        log(f"Лёгкая модель названия недоступна: {type(exc).__name__}; использую исходное название.")
        title = original_title
    fallback = f"Видео {video_id}"
    return safe_russian_filename(title, fallback=fallback)


def _validate_translation_payload(payload: Any, groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return pipeline.validate_translation(payload, groups)


def translate_groups_max(
    groups: list[dict[str, Any]],
    *,
    metadata: dict[str, Any],
    caption_origin: str,
    model_name: str,
) -> list[dict[str, Any]]:
    source_json = json.dumps(groups, ensure_ascii=False, indent=2)
    context = {
        "video_title": metadata.get("title") or "",
        "channel": metadata.get("uploader") or metadata.get("channel") or "",
        "caption_origin": caption_origin,
    }
    draft_prompt = f"""
Ты — ведущий переводчик профессионального русского закадрового дубляжа.
Сделай буквально-литературный перевод исходной устной речи на естественный сильный русский язык.

Обязательные правила:
1. Сохрани каждое утверждение, отрицание, условие, причинно-следственную связь, имя, число, цитату и ссылку.
2. Ничего не добавляй, не поясняй и не богословствуй сверх оригинала.
3. Не калькируй синтаксис; русский должен звучать как живая речь того же автора.
4. Сохрани степень категоричности, юмор, резкость, риторику и терминологию области.
5. Учитывай весь контекст ролика, а не переводи блоки изолированно.
6. ID должны сохраниться один к одному; блоки нельзя объединять или дробить.
7. Текст предназначен для произнесения в исходном временном окне: пиши ёмко, но не теряй смысл.

Верни только JSON: {{"segments":[{{"id":1,"russian":"..."}}]}}
Контекст: {json.dumps(context, ensure_ascii=False)}
Исходные блоки: {source_json}
""".strip()
    draft = _validate_translation_payload(hardened.gemini_json(draft_prompt, model_name=model_name), groups)

    fidelity_prompt = f"""
Ты — независимый старший редактор перевода. Построчно сверь русский черновик с исходником.
Исправь только реальные ошибки: потерянную мысль, добавление, неверное отрицание, неточный термин,
ошибочное местоимение, нарушенную логику, кальку или неестественную русскую фразу.
Не украшай текст и не меняй авторскую позицию. ID сохрани один к одному.
Верни только JSON: {{"segments":[{{"id":1,"russian":"..."}}]}}

ОРИГИНАЛ:
{source_json}

ЧЕРНОВИК:
{json.dumps(draft, ensure_ascii=False, indent=2)}
""".strip()
    reviewed = _validate_translation_payload(hardened.gemini_json(fidelity_prompt, model_name=model_name), groups)

    final_prompt = f"""
Ты — выпускающий редактор русского дубляжа. Это последний контроль качества.
Сохрани точность уже сверенного перевода, но устрани оставшиеся тяжёлые конструкции, двусмысленность,
неверный регистр и фразы, которые невозможно естественно произнести в данном временном окне.
Нельзя выбрасывать утверждения, отрицания, имена, числа, причины или выводы. Нельзя добавлять пояснения.
ID строго один к одному. Верни только JSON: {{"segments":[{{"id":1,"russian":"..."}}]}}

ВРЕМЕННЫЕ ОКНА И ОРИГИНАЛ:
{source_json}

СВЕРЕННЫЙ ПЕРЕВОД:
{json.dumps(reviewed, ensure_ascii=False, indent=2)}
""".strip()
    final = _validate_translation_payload(hardened.gemini_json(final_prompt, model_name=model_name), groups)

    overloaded: list[dict[str, Any]] = []
    for source, translated in zip(groups, final, strict=True):
        available = max(1.0, float(source["end"]) - float(source["start"]))
        rate = len(translated["russian"].split()) / available
        if rate > 3.25:
            overloaded.append({
                "id": source["id"],
                "seconds": round(available, 3),
                "source": source.get("source") or source.get("english") or "",
                "russian": translated["russian"],
            })
    if overloaded:
        compression_prompt = f"""
Сократи только перегруженные русские реплики до естественной произносимой длины.
Не теряй ни одного утверждения, отрицания, имени, числа, причины или вывода; не превращай текст в конспект.
Убирай только словесную избыточность и кальки. Верни JSON только для перечисленных ID:
{{"segments":[{{"id":1,"russian":"..."}}]}}

{json.dumps(overloaded, ensure_ascii=False, indent=2)}
""".strip()
        compact = hardened.gemini_json(compression_prompt, model_name=model_name)
        compact_list = compact.get("segments") if isinstance(compact, dict) else compact
        compact_by_id = {
            int(item["id"]): re.sub(r"\s+", " ", str(item.get("russian") or "")).strip()
            for item in compact_list or []
            if isinstance(item, dict) and str(item.get("id", "")).isdigit() and str(item.get("russian") or "").strip()
        }
        for item in final:
            if item["id"] in compact_by_id:
                item["russian"] = compact_by_id[item["id"]]
    return final


def write_translation_template(
    groups: list[dict[str, Any]],
    path: Path,
    *,
    title: str,
    caption_origin: str,
    language: str,
) -> None:
    lines = [
        f"# {title}",
        "# Заполните текст после RU: в каждом блоке. ID, EN и таймкоды не меняйте.",
        "# Бот не переписывает ваш текст; он только проверяет полноту ID и произносимую длину.",
        f"# Источник расшифровки: {caption_origin}; язык: {language or 'не определён'}",
        "",
    ]
    for group in groups:
        lines.extend([
            f"[{group['id']}] {group['start']:.3f} --> {group['end']:.3f}",
            f"EN: {group.get('source') or group.get('english') or ''}",
            "RU:",
            "",
        ])
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def parse_custom_translation(text: str, groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        raise RuntimeError("Перевод пуст.")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if payload is not None:
        return _validate_translation_payload(payload, groups)

    blocks = list(re.finditer(r"(?ms)^\s*\[(\d+)\][^\n]*\n(.*?)(?=^\s*\[\d+\]|\Z)", raw))
    mapped: list[dict[str, Any]] = []
    for match in blocks:
        item_id = int(match.group(1))
        body = match.group(2).strip()
        ru_match = re.search(r"(?ms)^RU:\s*(.*)$", body)
        if ru_match:
            value = ru_match.group(1).strip()
        else:
            value = re.sub(r"(?ms)^EN:.*?(?:\n|$)", "", body).strip()
        value = re.sub(r"\s+", " ", value).strip()
        if value:
            mapped.append({"id": item_id, "russian": value})
    if mapped:
        return _validate_translation_payload(mapped, groups)

    compact_matches = re.findall(r"(?m)^\s*\[(\d+)\]\s+(.+?)\s*$", raw)
    if compact_matches:
        return _validate_translation_payload(
            [{"id": int(item_id), "russian": value.strip()} for item_id, value in compact_matches],
            groups,
        )

    paragraphs = [re.sub(r"\s+", " ", item).strip() for item in re.split(r"\n\s*\n", raw) if item.strip()]
    if len(paragraphs) == len(groups):
        return [{"id": int(group["id"]), "russian": paragraph} for group, paragraph in zip(groups, paragraphs, strict=True)]
    raise RuntimeError(
        f"Не удалось сопоставить перевод: нужно {len(groups)} блоков. "
        "Используйте присланный шаблон с метками [1], [2] и строками RU:."
    )


def validate_custom_timing(translations: list[dict[str, Any]], groups: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for source, translated in zip(groups, translations, strict=True):
        seconds = max(1.0, float(source["end"]) - float(source["start"]))
        words = len(str(translated["russian"]).split())
        rate = words / seconds
        if rate > 3.65:
            warnings.append(
                f"Блок [{source['id']}]: {words} слов на {seconds:.1f} сек. ({rate:.2f} слова/с) — сократите реплику."
            )
    return warnings


def _source_groups(cues: list[pipeline.Cue]) -> list[dict[str, Any]]:
    groups = pipeline.group_cues(cues)
    for group in groups:
        group["source"] = group.pop("english")
    return groups


def _hardlink_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _telegram_entry(
    path: Path,
    *,
    filename: str,
    label: str,
    primary: bool = False,
    video: bool = False,
    send_default: bool = True,
) -> dict[str, Any]:
    return {
        "path": str(path),
        "filename": filename,
        "label": label,
        "primary": primary,
        "video": video,
        "send_default": send_default,
    }


def _build_render_segments(
    groups: list[dict[str, Any]],
    translations: list[dict[str, Any]],
    *,
    delay_ms: int,
    duration: float,
) -> tuple[list[dict[str, Any]], list[pipeline.Cue]]:
    delay = delay_ms / 1000.0
    render_segments: list[dict[str, Any]] = []
    subtitles: list[pipeline.Cue] = []
    for index, (source, translated) in enumerate(zip(groups, translations, strict=True), start=1):
        start = float(source["start"])
        source_end = min(duration, float(source["end"]))
        render_end = max(start + 1.25, source_end - delay)
        profile = "composite" if index == len(groups) or index % 4 == 0 else "extended"
        render_segments.append({
            "id": index,
            "start": round(start, 3),
            "end": round(render_end, 3),
            "start_delay_ms": delay_ms,
            "reference_profile": profile,
            "tail_guard": 0.36 if profile == "extended" else 0.42,
            "text": translated["russian"],
            "source_end": round(source_end, 3),
            "source": source.get("source") or "",
        })
        subtitles.append(
            pipeline.Cue(min(duration, start + delay), source_end, translated["russian"])
        )
    return render_segments, subtitles


def _run_voxcpm_and_master(
    *,
    root: Path,
    request: dict[str, Any],
    source: Path,
    cues: list[pipeline.Cue],
    duration: float,
    segments_json: Path,
    final_mixed: Path,
    final_russian: Path,
) -> Path:
    reference_dir = root / "references"
    audio_dir = root / "audio"
    segment_work = root / "segment_work"
    master_work = root / "master_work"
    for directory in (reference_dir, audio_dir, segment_work, master_work):
        directory.mkdir(parents=True, exist_ok=True)

    extended_reference = reference_dir / "extended_reference.wav"
    composite_reference = reference_dir / "composite_reference.wav"
    extended_intervals, composite_intervals = pipeline.reference_intervals(cues, duration)
    pipeline.build_reference(source, extended_intervals, extended_reference, target_seconds=min(24.0, max(12.0, duration * 0.45)))
    pipeline.build_reference(source, composite_intervals, composite_reference, target_seconds=min(21.0, max(10.0, duration * 0.38)))

    repo = Path(__file__).resolve().parents[2]
    backend = get_backend(request.get("speech_backend") or DEFAULT_BACKEND_ID)
    missing = backend.capabilities().missing()
    if missing:
        raise RuntimeError(
            f"Speech backend {backend.backend_id} lacks production capabilities: {', '.join(missing)}."
        )
    runtime = backend.runtime_paths(repo, request)
    cpu_python = runtime.cpu_python
    if not cpu_python.is_file():
        raise RuntimeError(
            f"CPU Python не найден для backend={backend.backend_id}: {cpu_python}"
        )

    threads = max(1, int(request.get("threads") or 10))
    steps = max(1, int(request.get("steps") or 16))
    cfg = float(request.get("cfg") or 1.8)
    original_level = float(request.get("original_level") or 0.18)
    video_id = str(request["video_id"])
    russian_timeline = audio_dir / f"{video_id}_ru_timeline.wav"
    env = backend.process_environment(
        {"threads": threads, "speech_backend": backend.backend_id},
        base_environment=os.environ,
    ).as_dict(os.environ)

    log(f"=== {backend.backend_id} SPEECH / RENDER ===")
    synth = backend.build_renderer_command(
        runtime,
        values={
            "extended_reference": str(extended_reference),
            "composite_reference": str(composite_reference),
            "segments_json": str(segments_json),
            "segment_work": str(segment_work),
            "timeline": str(russian_timeline),
            "threads": str(threads),
            "steps": str(steps),
            "cfg": str(cfg),
            "cache_length": "4096",
            "duration": f"{duration:.6f}",
            "base_seed": str(int(request.get("base_seed") or 2026072800)),
        },
    )
    result = subprocess.run(synth, cwd=str(repo), env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"Speech backend {backend.backend_id} CPU-синтез завершился "
            f"с кодом {result.returncode}."
        )

    log("=== CONSTANT MIX / FINAL MASTER ===")
    master = backend.build_master_command(
        runtime,
        values={
            "source": str(source),
            "timeline": str(russian_timeline),
            "master_work": str(master_work),
            "final_mixed": str(final_mixed),
            "final_russian": str(final_russian),
            "original_level": f"{original_level:.6f}",
            "target_i": "-14.0",
            "target_lra": "9.0",
            "target_tp": "-1.0",
        },
    )
    result = subprocess.run(master, cwd=str(repo), env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Финальный master завершился с кодом {result.returncode}.")
    return russian_timeline


def _run_speech_and_master(**kwargs: Any) -> Path:
    """Generic engine hook; clean routes replace this before main()."""
    return _run_voxcpm_and_master(**kwargs)


def main() -> None:
    pipeline.configure_utf8()
    parser = argparse.ArgumentParser(description="Universal Dub Studio project runtime")
    parser.add_argument("-Mode", "--mode", choices=("gemini", "custom"), required=True)
    parser.add_argument("-PrepareOnly", "--prepare-only", action="store_true")
    args = parser.parse_args()

    hardened.install_runtime_adapters()
    project_id = current_project_id()
    root = project_root(project_id)
    request = load_request(root)
    mode = str(args.mode)
    if mode != str(request.get("translation_mode")):
        raise RuntimeError("Режим задания не совпадает с режимом проекта.")

    source_dir = root / "source"
    output_dir = root / "output"
    input_dir = root / "input"
    for directory in (source_dir, output_dir, input_dir):
        directory.mkdir(parents=True, exist_ok=True)

    source = source_dir / "source.mp4"
    source_url = str(request["source_url"])
    video_id = str(request["video_id"])
    whisper_model = str(request.get("whisper_model") or "large-v3")
    translation_model = str(request.get("translation_model") or "gemini-3.6-flash")
    title_model = str(request.get("title_model") or "gemini-3.5-flash-lite")

    log("=== 1. SOURCE / YOUTUBE ===")
    metadata = hardened.download_source(source_url, source)
    duration = pipeline.ffprobe_duration(source)
    log(f"Источник: {metadata.get('title') or video_id}")
    log(f"Длительность: {duration:.3f} сек.")

    log("=== 2. PREFERRED TRANSCRIPT ===")
    cues, caption_origin, source_language = acquire_transcript(
        source_url,
        source,
        source_dir,
        metadata,
        whisper_model=whisper_model,
        duration=duration,
    )
    groups = _source_groups(cues)
    source_srt = output_dir / "source_subtitles.srt"
    source_txt = output_dir / "source_transcript.txt"
    groups_json = root / "source_groups.json"
    pipeline.write_srt(cues, source_srt)
    source_txt.write_text("\n\n".join(f"[{g['id']}] {g['source']}" for g in groups) + "\n", encoding="utf-8")
    save_json(groups_json, groups)

    log("=== 3. RUSSIAN TITLE ===")
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
            "translation_mode": mode,
            "caption_origin": caption_origin,
            "source_language": source_language,
            "russian_title": russian_title,
        },
    )
    log(f"Название: {russian_title}")

    template = output_dir / "translation_template.txt"
    write_translation_template(
        groups,
        template,
        title=russian_title,
        caption_origin=caption_origin,
        language=source_language,
    )

    base_manifest: dict[str, Any] = {
        "schema_version": 2,
        "project_id": project_id,
        "video_id": video_id,
        "source_url": source_url,
        "original_title": metadata.get("title") or "",
        "russian_title": russian_title,
        "channel": metadata.get("uploader") or metadata.get("channel") or "",
        "duration_seconds": round(duration, 4),
        "caption_origin": caption_origin,
        "source_language": source_language,
        "translation_mode": mode,
        "original_level": float(request.get("original_level") or 0.18),
        "russian_delay_ms": int(request.get("russian_delay_ms") or 420),
    }

    if args.prepare_only:
        manifest_path = output_dir / "manifest.json"
        base_manifest.update({
            "phase": "awaiting_custom_translation",
            "telegram_outputs": [
                _telegram_entry(template, filename=f"{russian_title} — шаблон перевода.txt", label="Шаблон для вашего перевода"),
                _telegram_entry(source_txt, filename=f"{russian_title} — точная расшифровка.txt", label=f"Исходная расшифровка ({caption_origin})"),
                _telegram_entry(source_srt, filename=f"{russian_title} — исходные субтитры.srt", label="Исходные субтитры с таймкодами"),
            ],
        })
        save_json(manifest_path, base_manifest)
        log("=== ПОДГОТОВКА ГОТОВА: ОЖИДАЕТСЯ ПЕРЕВОД ПОЛЬЗОВАТЕЛЯ ===")
        return

    log("=== 4. RUSSIAN TRANSLATION ===")
    qa_path = output_dir / "translation_qa.txt"
    if mode == "gemini":
        translations = translate_groups_max(
            groups,
            metadata=metadata,
            caption_origin=caption_origin,
            model_name=translation_model,
        )
        qa_path.write_text(
            "Gemini MAX: три редакторских прохода + отдельная компрессия только перегруженных реплик.\n",
            encoding="utf-8",
        )
    else:
        custom_json = input_dir / "custom_translation.json"
        custom_txt = input_dir / "custom_translation.txt"
        if custom_json.is_file():
            translations = _validate_translation_payload(
                json.loads(custom_json.read_text(encoding="utf-8-sig")),
                groups,
            )
        elif custom_txt.is_file():
            translations = parse_custom_translation(custom_txt.read_text(encoding="utf-8-sig"), groups)
        else:
            raise RuntimeError("Не загружен пользовательский перевод. Используйте /dubtranslation.")
        timing_warnings = validate_custom_timing(translations, groups)
        qa_path.write_text(
            ("Ваш перевод сохранён без переписывания моделью.\n" + "\n".join(timing_warnings)).rstrip() + "\n",
            encoding="utf-8",
        )
        if timing_warnings:
            raise RuntimeError(
                "Пользовательский перевод не помещается в тайминг:\n" + "\n".join(timing_warnings[:12])
            )

    translation_json = root / "translation_final.json"
    translation_txt = output_dir / "russian_translation.txt"
    save_json(translation_json, {"segments": translations})
    translation_txt.write_text(
        "\n\n".join(f"[{item['id']}] {item['russian']}" for item in translations) + "\n",
        encoding="utf-8",
    )

    delay_ms = int(request.get("russian_delay_ms") or 420)
    render_segments, russian_cues = _build_render_segments(
        groups,
        translations,
        delay_ms=delay_ms,
        duration=duration,
    )
    segments_json = root / "segments_ru_final.json"
    russian_srt = output_dir / "russian_subtitles.srt"
    save_json(segments_json, render_segments)
    pipeline.write_srt(russian_cues, russian_srt)

    stable_mixed = output_dir / "final_upload.mp4"
    stable_russian = output_dir / "russian_only.mp4"
    russian_timeline = _run_speech_and_master(
        root=root,
        request=request,
        source=source,
        cues=cues,
        duration=duration,
        segments_json=segments_json,
        final_mixed=stable_mixed,
        final_russian=stable_russian,
    )

    named_mixed = output_dir / f"{russian_title} — русский дубляж.mp4"
    named_russian = output_dir / f"{russian_title} — только русский голос.mp4"
    named_srt = output_dir / f"{russian_title} — русские субтитры.srt"
    named_translation = output_dir / f"{russian_title} — перевод.txt"
    _hardlink_or_copy(stable_mixed, named_mixed)
    _hardlink_or_copy(stable_russian, named_russian)
    _hardlink_or_copy(russian_srt, named_srt)
    _hardlink_or_copy(translation_txt, named_translation)

    manifest_path = output_dir / "manifest.json"
    base_manifest.update({
        "phase": "completed",
        "translation_model": translation_model if mode == "gemini" else "user",
        "translation_passes": 3 if mode == "gemini" else 0,
        "segments": len(render_segments),
        "outputs": {
            "mixed": str(named_mixed),
            "russian_only": str(named_russian),
            "russian_srt": str(named_srt),
            "source_srt": str(source_srt),
            "translation": str(named_translation),
            "qa": str(qa_path),
            "russian_timeline": str(russian_timeline),
        },
        "telegram_outputs": [
            _telegram_entry(named_mixed, filename=named_mixed.name, label="Готовый ролик: оригинал 18%, русский с задержкой", primary=True, video=True),
            _telegram_entry(named_russian, filename=named_russian.name, label="Версия только с русским голосом", video=True, send_default=False),
            _telegram_entry(named_srt, filename=named_srt.name, label="Русские субтитры"),
            _telegram_entry(named_translation, filename=named_translation.name, label="Итоговый русский перевод"),
            _telegram_entry(source_srt, filename=f"{russian_title} — исходные субтитры.srt", label=f"Исходные субтитры ({caption_origin})"),
            _telegram_entry(qa_path, filename=f"{russian_title} — проверка перевода.txt", label="Отчёт контроля перевода"),
        ],
    })
    save_json(manifest_path, base_manifest)
    log("=== ГОТОВО ===")
    log(f"Mixed: {named_mixed}")
    log(f"Russian-only: {named_russian}")


if __name__ == "__main__":
    main()
