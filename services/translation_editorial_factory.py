#!/usr/bin/env python3
"""Factory bridge for full-sermon translation editorial review."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import zipfile
from pathlib import Path
from typing import Any

from core.globals import DOWNLOAD_DIR
from services.async_process import run_cancellable_process
from services.ffmpeg import YTDLP_BASE_ARGS
from services.translation_editorial import (
    REVIEW_SCHEMA_NAME,
    REVIEW_SCHEMA_VERSION,
    build_review_pack,
    load_pack_manifest,
    transcribe_russian_whisper,
    validate_review_document,
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


def _editorial_root(media_id: str) -> Path:
    root = DOWNLOAD_DIR / "translation_editorial" / str(media_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


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
    languages = ",".join(language_order)
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


def _expected_candidate_ids(manifest: dict[str, Any]) -> set[str]:
    return {
        str(item.get("candidate_id"))
        for group in (manifest.get("candidates") or {}).values()
        if isinstance(group, list)
        for item in group
        if isinstance(item, dict) and item.get("candidate_id")
    }


def _candidate_coverage_errors(review: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    expected = _expected_candidate_ids(manifest)
    reviewed = {
        str(item.get("candidate_id"))
        for item in review.get("candidate_reviews") or []
        if isinstance(item, dict) and item.get("candidate_id")
    }
    errors: list[str] = []
    missing = sorted(expected - reviewed)
    extra = sorted(reviewed - expected)
    if missing:
        errors.append("missing candidate reviews: " + ", ".join(missing))
    if extra:
        errors.append("unexpected candidate reviews: " + ", ".join(extra))
    return errors


def _gemini_max_attempts() -> int:
    try:
        value = int(os.getenv("SHORTS_FACTORY_EDITORIAL_GEMINI_MAX_ATTEMPTS", "1") or "1")
    except (TypeError, ValueError):
        value = 1
    return max(1, min(value, 2))


async def generate_gemini_editorial_review(pack_path: Path) -> dict[str, Any] | None:
    """Run a quota-bounded full-sermon review on exact Gemini 3.6/high only."""
    try:
        from core.globals import GEMINI_CLIENTS, make_text_config_smart
    except Exception:
        return None
    if not GEMINI_CLIENTS:
        return None

    manifest = load_pack_manifest(pack_path)
    original = _read_pack_text(pack_path, "original.srt")
    russian = _read_pack_text(pack_path, "russian_whisper.srt")
    candidates = _read_pack_text(pack_path, "candidates.json")
    prompt = (
        "Ты редактор переведённой проповеди. Сравни исходный SRT с фактически услышанной "
        "русской речью из Whisper SRT. Проверяй смысл, а не литературную красоту. "
        "Небольшая неестественность русского допустима, если смысл сохранён. Особое внимание "
        "к отрицаниям, субъекту и объекту действия, причинно-следственным связям, именам, "
        "числам, местам Писания и богословским терминам. Не придумывай ошибок, которых нет "
        "в русской Whisper-стенограмме. Таймкоды issue всегда бери из русского SRT. "
        "drop_span выбирай только когда удаление короткого дефекта оставляет исходную мысль "
        "целой; mute_span только для чисто звукового дефекта; иначе reject_region. "
        "Для каждого candidate_id обязательно верни ровно одну оценку пригодности с учётом "
        "найденных дефектов. Верни только JSON по схеме.\n\n"
        f"MANIFEST:\n{json.dumps(manifest, ensure_ascii=False)}\n\n"
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
    timeout = float(os.getenv("SHORTS_FACTORY_EDITORIAL_GEMINI_TIMEOUT_SEC", "300") or "300")
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
            errors.extend(_candidate_coverage_errors(review, manifest))
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
    original_srt = await download_original_srt(
        url,
        root,
        language=source_language or "en",
    )
    russian_srt = root / "russian_whisper.srt"
    russian_words = root / "russian_whisper_words.json"
    await transcribe_russian_whisper(
        Path(translated_video_path),
        srt_output=russian_srt,
        words_output=russian_words,
        ai_data=ai_data,
        model_name="large-v3",
    )
    pack = build_review_pack(
        output_dir=root,
        media_id=media_id,
        source_url=url,
        title=title,
        performer=performer,
        duration=duration,
        source_video_path=Path(translated_video_path),
        original_srt_path=original_srt,
        russian_whisper_srt_path=russian_srt,
        russian_words_path=russian_words,
        shorts_candidates=shorts_candidates,
        long_candidates=long_candidates,
    )

    review_path: Path | None = None
    markdown_path: Path | None = None
    if factory_editorial_gemini_enabled():
        review = await generate_gemini_editorial_review(pack)
        if review is not None:
            review_path = root / f"{media_id}_gemini_review.json"
            markdown_path = root / f"{media_id}_gemini_review.md"
            review_path.write_text(
                json.dumps(review, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            markdown_path.write_text(render_review_markdown(review), encoding="utf-8")
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
                "large-v3 + кандидаты. Этот ZIP можно прислать ChatGPT для полной редакционной проверки."
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
