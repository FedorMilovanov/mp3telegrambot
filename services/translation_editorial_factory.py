#!/usr/bin/env python3
"""Factory bridge for full-sermon translation editorial review."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from core.globals import DOWNLOAD_DIR
from services.async_process import run_cancellable_process
from services.ffmpeg import YTDLP_BASE_ARGS
from services.media_delivery_probe import media_probe_is_deliverable, probe_media_async
from services.translation_editorial import (
    REVIEW_SCHEMA_NAME,
    REVIEW_SCHEMA_VERSION,
    build_review_pack,
    sha256_file,
    transcribe_russian_whisper,
    validate_review_document,
)
from services.translation_editorial_pack_contract import (
    load_verified_review_pack as load_pack_manifest,
)

logger = logging.getLogger(__name__)

FACTORY_EDITORIAL_GEMINI_MODEL = "gemini-3.6-flash"


def _enabled(name: str, default: bool) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def factory_editorial_pack_enabled() -> bool:
    return _enabled("SHORTS_FACTORY_EDITORIAL_REVIEW_PACK", True)


def factory_editorial_gemini_enabled() -> bool:
    return _enabled("SHORTS_FACTORY_EDITORIAL_GEMINI", False)


def _safe_media_id(media_id: str) -> str:
    safe = "".join(
        char if (char.isalnum() or char in "_-") else "_"
        for char in str(media_id or "media")
    )
    return safe[:100] or "media"


def _editorial_root(media_id: str) -> Path:
    root = DOWNLOAD_DIR / "translation_editorial" / _safe_media_id(media_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _configured_livedub_delay_seconds() -> float:
    try:
        delay_ms = float(os.getenv("LIVEDUB_DELAY_MS", "600") or "600")
    except (TypeError, ValueError):
        delay_ms = 600.0
    if not math.isfinite(delay_ms):
        delay_ms = 600.0
    return max(0.0, min(delay_ms, 5000.0)) / 1000.0


def _configured_factory_shift_extra_seconds() -> float:
    try:
        value = float(os.getenv("SHORTS_FACTORY_LIVEDUB_SHIFT_EXTRA_SEC", "0.15") or "0.15")
    except (TypeError, ValueError):
        value = 0.15
    if not math.isfinite(value):
        value = 0.15
    return max(0.0, min(value, 5.0))


def _timeline_metadata() -> dict[str, Any]:
    delay = _configured_livedub_delay_seconds()
    extra = _configured_factory_shift_extra_seconds()
    return {
        "original_srt": "source_original_timeline",
        "russian_whisper": "translated_video_timeline",
        "factory_candidates": "translated_video_timeline",
        "configured_russian_delay_seconds": round(delay, 3),
        "factory_candidate_extra_shift_seconds": round(extra, 3),
        "comparison_rule": (
            "Original and Russian cues are not assumed to have equal timestamps. "
            "Use semantic sequence plus this configured delay as alignment evidence; "
            "all review issue timestamps must target the translated-video/Russian timeline."
        ),
    }


def _copy_without_overwrite(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
        return
    except FileExistsError:
        raise
    except OSError:
        pass
    try:
        with source.open("rb") as input_stream, destination.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=4 * 1024 * 1024)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _durable_review_source(source_path: Path, root: Path, media_id: str) -> Path:
    """Keep review source outside Factory's short-lived *_factory_source cleanup glob."""
    source_path = Path(source_path)
    if not source_path.exists() or source_path.stat().st_size <= 1024:
        raise FileNotFoundError(f"Factory editorial source missing/empty: {source_path}")
    suffix = source_path.suffix.lower() or ".mp4"
    destination = root / f"{_safe_media_id(media_id)}_editorial_source{suffix}"
    if destination.exists():
        if destination.stat().st_size != source_path.stat().st_size:
            raise RuntimeError("durable editorial source size conflicts with current Factory source")
        if sha256_file(destination) != sha256_file(source_path):
            raise RuntimeError("durable editorial source bytes conflict with current Factory source")
        return destination
    _copy_without_overwrite(source_path, destination)
    if destination.stat().st_size != source_path.stat().st_size:
        destination.unlink(missing_ok=True)
        raise RuntimeError("durable editorial source copy has wrong size")
    if sha256_file(destination) != sha256_file(source_path):
        destination.unlink(missing_ok=True)
        raise RuntimeError("durable editorial source copy has wrong SHA-256")
    return destination


async def download_original_srt(
    video_url: str,
    output_dir: Path,
    *,
    language: str = "en",
) -> Path:
    """Fetch exact provider subtitles as SRT, manual first then auto captions."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for old in output_dir.glob("editorial_original*.srt"):
        old.unlink(missing_ok=True)

    lang_root = (language or "en").split("-", 1)[0].lower()
    language_order = [f"{lang_root}.*", lang_root]
    if lang_root != "en":
        language_order.extend(["en.*", "en"])
    languages = ",".join(dict.fromkeys(language_order))
    template = output_dir / "editorial_original_%(id)s.%(ext)s"

    async def attempt(auto: bool) -> Path | None:
        command = [
            *YTDLP_BASE_ARGS,
            "--skip-download",
            "--write-auto-subs" if auto else "--write-subs",
            "--sub-langs",
            languages,
            "--sub-format",
            "srt/best",
            "--convert-subs",
            "srt",
            "--output",
            str(template),
            video_url,
        ]
        result = await run_cancellable_process(command, timeout=300, text=True)
        if result.returncode != 0:
            return None
        candidates = sorted(
            output_dir.glob("editorial_original*.srt"),
            key=lambda item: item.stat().st_size,
            reverse=True,
        )
        return candidates[0] if candidates else None

    for auto in (False, True):
        candidate = await attempt(auto)
        if candidate is not None and candidate.stat().st_size > 0:
            return candidate
    raise RuntimeError("original source SRT is unavailable")


def _read_pack_text(pack_path: Path, name: str) -> str:
    with zipfile.ZipFile(Path(pack_path), "r") as archive:
        return archive.read(name).decode("utf-8", errors="replace")


def _gemini_schema() -> dict[str, Any]:
    action_schema = {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["drop_span", "mute_span", "reject_region"],
            }
        },
        "required": ["type"],
    }
    issue_schema = {
        "type": "object",
        "properties": {
            "start_seconds": {"type": "number"},
            "end_seconds": {"type": "number"},
            "severity": {
                "type": "string",
                "enum": ["roughness", "minor", "major", "critical"],
            },
            "category": {"type": "string"},
            "observed_ru": {"type": "string"},
            "source_meaning": {"type": "string"},
            "suggested_replacement_text": {"type": "string"},
            "rationale": {"type": "string"},
            "action": action_schema,
        },
        "required": [
            "start_seconds",
            "end_seconds",
            "severity",
            "category",
            "observed_ru",
            "source_meaning",
            "rationale",
            "action",
        ],
    }
    candidate_schema = {
        "type": "object",
        "properties": {
            "candidate_id": {"type": "string"},
            "verdict": {"type": "string", "enum": ["keep", "repair", "reject"]},
            "reason": {"type": "string"},
            "issues": {"type": "array", "items": issue_schema},
        },
        "required": ["candidate_id", "verdict", "reason", "issues"],
    }
    return {
        "type": "object",
        "properties": {
            "full_sermon": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["keep", "repair", "reject"]},
                    "reason": {"type": "string"},
                    "issues": {"type": "array", "items": issue_schema},
                },
                "required": ["verdict", "reason", "issues"],
            },
            "candidate_reviews": {"type": "array", "items": candidate_schema},
        },
        "required": ["full_sermon", "candidate_reviews"],
    }


def _strip_json_fence(value: Any) -> str:
    raw = str(value or "").strip()
    if raw.startswith("```"):
        _first, separator, rest = raw.partition("\n")
        raw = rest if separator else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3].rstrip()
    return raw.strip()


def _gemini_max_attempts() -> int:
    try:
        value = int(os.getenv("SHORTS_FACTORY_EDITORIAL_GEMINI_MAX_ATTEMPTS", "1") or "1")
    except (TypeError, ValueError):
        value = 1
    return max(1, min(value, 2))


def _manifest_for_model(manifest: dict[str, Any]) -> dict[str, Any]:
    """Remove machine-local paths before sending semantic evidence to Gemini."""
    copied = json.loads(json.dumps(manifest, ensure_ascii=False))
    translated = ((copied.get("source") or {}).get("translated_video") or {})
    if isinstance(translated, dict):
        translated.pop("local_path", None)
    return copied


async def generate_gemini_editorial_review(pack_path: Path) -> dict[str, Any] | None:
    """Run a quota-bounded full-sermon review on exact Gemini 3.6/high only."""
    try:
        from core.globals import GEMINI_CLIENTS, make_text_config_smart
    except Exception:
        return None
    if not GEMINI_CLIENTS:
        return None

    manifest = load_pack_manifest(pack_path)
    model_manifest = _manifest_for_model(manifest)
    original = _read_pack_text(pack_path, "original.srt")
    russian = _read_pack_text(pack_path, "russian_whisper.srt")
    candidates = _read_pack_text(pack_path, "candidates.json")
    prompt = (
        "Ты редактор переведённой проповеди. Сравни исходный SRT с фактически услышанной "
        "русской речью из Whisper SRT. Проверяй смысл, а не литературную красоту. "
        "Небольшая неестественность русского допустима, если смысл сохранён. Особое внимание "
        "к отрицаниям, субъекту и объекту действия, причинно-следственным связям, именам, "
        "числам, местам Писания и богословским терминам. Не придумывай ошибок, которых нет "
        "в русской Whisper-стенограмме. В manifest.timeline явно описаны разные временные "
        "шкалы: не сопоставляй реплики по одинаковому номеру cue или одинаковой секунде; "
        "сопоставляй по смысловой последовательности и указанной задержке. Таймкоды issue "
        "всегда бери из Russian Whisper / translated-video timeline. drop_span выбирай только "
        "когда удаление короткого дефекта оставляет исходную мысль целой; mute_span только "
        "для чисто звукового дефекта; иначе reject_region. Для каждого candidate_id обязательно "
        "верни ровно одну оценку пригодности. Верни только JSON по схеме.\n\n"
        f"MANIFEST:\n{json.dumps(model_manifest, ensure_ascii=False)}\n\n"
        f"CANDIDATES:\n{candidates}\n\n"
        f"ORIGINAL SRT:\n{original}\n\n"
        f"RUSSIAN WHISPER SRT:\n{russian}"
    )
    config = make_text_config_smart(
        max_output_tokens=12000,
        model_name=FACTORY_EDITORIAL_GEMINI_MODEL,
        thinking_level="high",
        response_mime_type="application/json",
        response_schema=_gemini_schema(),
    )
    try:
        timeout = float(os.getenv("SHORTS_FACTORY_EDITORIAL_GEMINI_TIMEOUT_SEC", "300") or "300")
    except (TypeError, ValueError):
        timeout = 300.0
    if not math.isfinite(timeout):
        timeout = 300.0
    timeout = max(60.0, min(timeout, 600.0))

    clients = list(GEMINI_CLIENTS)[: _gemini_max_attempts()]
    for client_index, client in enumerate(clients, 1):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=FACTORY_EDITORIAL_GEMINI_MODEL,
                    contents=prompt,
                    config=config,
                ),
                timeout=timeout,
            )
            data = json.loads(_strip_json_fence(getattr(response, "text", "")))
            if not isinstance(data, dict):
                continue
            review = {
                "schema_name": REVIEW_SCHEMA_NAME,
                "schema_version": REVIEW_SCHEMA_VERSION,
                "review_pack_id": manifest.get("review_pack_id"),
                "reviewer": f"gemini:{FACTORY_EDITORIAL_GEMINI_MODEL}",
                "full_sermon": data.get("full_sermon"),
                "candidate_reviews": data.get("candidate_reviews") or [],
            }
            errors = validate_review_document(review, manifest)
            if errors:
                logger.warning(
                    "Factory editorial Gemini review rejected client=%d: %s",
                    client_index,
                    "; ".join(errors[:8]),
                )
                continue
            return review
        except Exception as exc:
            logger.info(
                "Factory editorial Gemini soft-fail client=%d model=%s: %s",
                client_index,
                FACTORY_EDITORIAL_GEMINI_MODEL,
                str(exc)[:180],
            )
    return None


def render_review_markdown(review: dict[str, Any]) -> str:
    full = review.get("full_sermon") or {}
    lines = [
        "# Translation Editorial Review",
        "",
        f"Full sermon: **{str(full.get('verdict') or 'unknown').upper()}**",
    ]
    reason = str(full.get("reason") or "").strip()
    if reason:
        lines.append(reason)
    issues = full.get("issues") or []
    if issues:
        lines.extend(["", "## Full-sermon issues"])
        for item in issues:
            if not isinstance(item, dict):
                continue
            action = item.get("action") or {}
            lines.append(
                "- "
                f"{float(item.get('start_seconds') or 0):.3f}–"
                f"{float(item.get('end_seconds') or 0):.3f}s · "
                f"{item.get('severity')} · {item.get('category')} · "
                f"{action.get('type')}: {item.get('rationale')}"
            )
    candidates = review.get("candidate_reviews") or []
    if candidates:
        lines.extend(["", "## Candidates"])
        for item in candidates:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- `{item.get('candidate_id')}` — **{str(item.get('verdict') or '').upper()}**: "
                f"{item.get('reason') or ''}"
            )
    return "\n".join(lines).rstrip() + "\n"


def _write_immutable_review_files(
    root: Path,
    media_id: str,
    pack_path: Path,
    review: dict[str, Any],
) -> tuple[Path, Path]:
    review_json = json.dumps(review, ensure_ascii=False, indent=2, allow_nan=False)
    digest = hashlib.sha256(review_json.encode("utf-8")).hexdigest()[:12]
    pack_digest = load_pack_manifest(pack_path)["review_pack_id"][7:19]
    safe_id = _safe_media_id(media_id)
    review_path = root / f"{safe_id}_{pack_digest}_{digest}_gemini_review.json"
    markdown_path = root / f"{safe_id}_{pack_digest}_{digest}_gemini_review.md"
    markdown = render_review_markdown(review)
    for path, content in ((review_path, review_json), (markdown_path, markdown)):
        if path.exists():
            if path.read_text(encoding="utf-8") != content:
                raise FileExistsError(f"immutable editorial review path collision: {path}")
        else:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(content)
    return review_path, markdown_path


async def prepare_factory_editorial_review(
    *,
    url: str,
    media_id: str,
    title: str,
    performer: str,
    duration: float,
    source_language: str,
    translated_video_path: Path,
    shorts_candidates: list[dict[str, Any]],
    long_candidates: list[dict[str, Any]],
    ai_data: dict[str, Any] | None = None,
) -> tuple[Path, Path | None, Path | None]:
    """Create the real transcript review pack and optional one-call Gemini review."""
    root = _editorial_root(media_id)
    source_path = Path(translated_video_path)
    probe = await probe_media_async(source_path)
    if not media_probe_is_deliverable(probe):
        raise RuntimeError("Factory editorial source failed video+audio media probe")
    assert probe is not None
    actual_duration = float(probe.duration)
    if not math.isfinite(actual_duration) or actual_duration <= 0:
        raise RuntimeError("Factory editorial source returned an invalid media duration")
    try:
        caller_duration = float(duration)
    except (TypeError, ValueError):
        caller_duration = 0.0
    if math.isfinite(caller_duration) and caller_duration > 0:
        if abs(actual_duration - caller_duration) > 1.25:
            raise RuntimeError(
                f"Factory editorial source duration drift: caller={caller_duration:.3f}s "
                f"probe={actual_duration:.3f}s"
            )

    durable_source = await asyncio.to_thread(_durable_review_source, source_path, root, media_id)
    safe_id = _safe_media_id(media_id)
    with tempfile.TemporaryDirectory(prefix=f".{safe_id}_editorial_run_", dir=root) as staging_name:
        staging = Path(staging_name)
        original_srt = await download_original_srt(
            url,
            staging,
            language=source_language or "en",
        )
        russian_srt = staging / "russian_whisper.srt"
        russian_words = staging / "russian_whisper_words.json"
        await transcribe_russian_whisper(
            durable_source,
            srt_output=russian_srt,
            words_output=russian_words,
            ai_data=ai_data,
            model_name="large-v3",
        )
        pack = await asyncio.to_thread(
            build_review_pack,
            output_dir=root,
            media_id=media_id,
            source_url=url,
            title=title,
            performer=performer,
            duration=actual_duration,
            source_video_path=durable_source,
            original_srt_path=original_srt,
            russian_whisper_srt_path=russian_srt,
            russian_words_path=russian_words,
            shorts_candidates=shorts_candidates,
            long_candidates=long_candidates,
            timeline_metadata=_timeline_metadata(),
        )
        await asyncio.to_thread(load_pack_manifest, pack)

    review_path: Path | None = None
    markdown_path: Path | None = None
    if factory_editorial_gemini_enabled():
        review = await generate_gemini_editorial_review(pack)
        if review is not None:
            review_path, markdown_path = _write_immutable_review_files(
                root,
                media_id,
                pack,
                review,
            )
    return pack, review_path, markdown_path


async def send_factory_editorial_files(
    update: Any,
    *,
    pack_path: Path,
    review_path: Path | None = None,
    markdown_path: Path | None = None,
) -> None:
    message = getattr(update, "message", None)
    if message is None:
        return
    with Path(pack_path).open("rb") as stream:
        await message.reply_document(
            document=stream,
            filename=Path(pack_path).name,
            caption=(
                "🔎 Translation Editorial Review: оригинальный SRT + Russian Whisper "
                "large-v3 + кандидаты. ZIP привязан к точным исходным байтам и может быть "
                "передан ChatGPT для полной редакционной проверки."
            ),
        )
    if review_path is not None and Path(review_path).exists():
        with Path(review_path).open("rb") as stream:
            await message.reply_document(
                document=stream,
                filename=Path(review_path).name,
                caption=f"🧠 Gemini {FACTORY_EDITORIAL_GEMINI_MODEL} · HIGH — редакционный review.json",
            )
    if markdown_path is not None and Path(markdown_path).exists():
        with Path(markdown_path).open("rb") as stream:
            await message.reply_document(
                document=stream,
                filename=Path(markdown_path).name,
                caption="📋 Читаемая сводка редакционного аудита",
            )


__all__ = [
    "FACTORY_EDITORIAL_GEMINI_MODEL",
    "download_original_srt",
    "factory_editorial_gemini_enabled",
    "factory_editorial_pack_enabled",
    "generate_gemini_editorial_review",
    "prepare_factory_editorial_review",
    "render_review_markdown",
    "send_factory_editorial_files",
]
