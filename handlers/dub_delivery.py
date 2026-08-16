#!/usr/bin/env python3
"""Delivery commands and automatic result sender for Dub Studio projects."""
from __future__ import annotations

from core.media_title_policy import canonical_delivery_filename

import html
import json
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, filters

from core.database import ADMIN_IDS
from services.dub_studio import DubStore, load_recipe, resolve_recipe_path, studio_root

_MSG_ONLY = filters.UpdateType.MESSAGE
_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}
_DOCUMENT_SUFFIXES = {".srt", ".ass", ".txt", ".json", ".wav", ".md"}


def _short(value: str, limit: int = 180) -> str:
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[: max(1, limit - 1)].rstrip() + "…"


async def _admin(update: Update) -> bool:
    user = update.effective_user
    if user and user.id in ADMIN_IDS:
        return True
    if update.effective_message:
        await update.effective_message.reply_text("⛔ Отправка Dub Studio доступна только администратору.")
    return False


def _resolve_project(store: DubStore, user_id: int, token: str | None) -> dict[str, Any]:
    token = str(token or "").strip().lower()
    if token and token not in {"last", "последний", "all", "все"}:
        project = store.get_project(token)
    else:
        project = store.latest_project(owner_user_id=user_id)
    if not project:
        raise KeyError("У вас ещё нет Dub Studio проектов.")
    if int(project["owner_user_id"]) != int(user_id):
        raise PermissionError("Это не ваш проект.")
    return project


def _dynamic_project_root(project: dict[str, Any]) -> Path:
    root = (studio_root() / "projects" / str(project["id"])).resolve()
    allowed = (studio_root() / "projects").resolve()
    root.relative_to(allowed)
    return root


def _dynamic_outputs(project: dict[str, Any], *, include_all_video: bool) -> list[dict[str, Any]]:
    root = _dynamic_project_root(project)
    manifest_path = root / "output" / "manifest.json"
    if not manifest_path.is_file():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    specs = manifest.get("telegram_outputs") if isinstance(manifest, dict) else None
    if not isinstance(specs, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, spec in enumerate(specs):
        if not isinstance(spec, dict):
            continue
        path = Path(str(spec.get("path") or "")).expanduser().resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if not path.is_file() or path.stat().st_size <= 0:
            continue
        resolved = str(path).casefold()
        if resolved in seen:
            continue
        seen.add(resolved)
        suffix = path.suffix.casefold()
        video = bool(spec.get("video")) or suffix in _VIDEO_SUFFIXES
        primary = bool(spec.get("primary"))
        send_default = bool(spec.get("send_default", True))
        if video and not include_all_video and not primary:
            continue
        if not include_all_video and not send_default:
            continue
        if not video and suffix not in _DOCUMENT_SUFFIXES:
            continue
        filename = Path(str(spec.get("filename") or path.name)).name
        rows.append({
            "name": str(spec.get("name") or f"dynamic_{index}"),
            "label": str(spec.get("label") or filename),
            "path": path,
            "filename": filename,
            "primary": primary,
            "video": video,
        })
    rows.sort(key=lambda item: (not item["primary"], not item["video"], item["name"]))
    return rows


def _recipe_outputs(project: dict[str, Any], *, include_all_video: bool) -> list[dict[str, Any]]:
    recipe = load_recipe(str(project["recipe_id"]))
    work_root = str(project.get("work_root") or "")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, spec in recipe.outputs.items():
        raw_path = str(spec.get("path") or "")
        if not raw_path:
            continue
        path = resolve_recipe_path(raw_path, work_root=work_root)
        if not path.is_file() or path.stat().st_size <= 0:
            continue
        resolved = str(path.resolve()).casefold()
        if resolved in seen:
            continue
        seen.add(resolved)
        suffix = path.suffix.lower()
        is_video = suffix in _VIDEO_SUFFIXES
        is_primary = bool(spec.get("primary"))
        if is_video and not is_primary and not include_all_video:
            continue
        if not is_video and suffix not in _DOCUMENT_SUFFIXES:
            continue
        rows.append({
            "name": str(name),
            "label": str(spec.get("label") or name),
            "path": path,
            "filename": path.name,
            "primary": is_primary,
            "video": is_video,
        })
    rows.sort(key=lambda item: (not item["primary"], not item["video"], item["name"]))
    return rows


def available_outputs(project: dict[str, Any], *, include_all_video: bool = False) -> list[dict[str, Any]]:
    dynamic = _dynamic_outputs(project, include_all_video=include_all_video)
    rows = dynamic if dynamic else _recipe_outputs(project, include_all_video=include_all_video)
    for row in rows:
        row["filename"] = canonical_delivery_filename(row.get("filename") or "")
    return rows


async def send_project_outputs(
    bot: Any,
    chat_id: int,
    project: dict[str, Any],
    *,
    include_all_video: bool = False,
) -> tuple[int, list[str]]:
    outputs = available_outputs(project, include_all_video=include_all_video)
    if not outputs:
        return 0, ["Готовые файлы ещё не найдены."]
    sent = 0
    failures: list[str] = []
    for item in outputs:
        path: Path = item["path"]
        label = _short(str(item["label"]), 800)
        filename = str(item.get("filename") or path.name)
        try:
            with path.open("rb") as handle:
                if item["video"]:
                    await bot.send_video(
                        chat_id=chat_id,
                        video=handle,
                        filename=filename,
                        caption=label,
                        supports_streaming=True,
                        write_timeout=1800,
                        read_timeout=1800,
                        connect_timeout=120,
                        pool_timeout=120,
                    )
                else:
                    await bot.send_document(
                        chat_id=chat_id,
                        document=handle,
                        filename=filename,
                        caption=label,
                        write_timeout=1800,
                        read_timeout=1800,
                        connect_timeout=120,
                        pool_timeout=120,
                    )
            sent += 1
        except Exception as exc:
            failures.append(f"{filename}: {_short(str(exc), 180)}")
    return sent, failures


async def dubsend_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    args = list(context.args or [])
    include_all = any(str(arg).strip().lower() in {"all", "все"} for arg in args)
    token = next((arg for arg in args if str(arg).strip().lower() not in {"all", "все"}), None)
    try:
        project = _resolve_project(DubStore(), update.effective_user.id, token)
        status = await update.effective_message.reply_text(
            f"📤 Отправляю результаты проекта <code>{html.escape(str(project['id']))}</code>…",
            parse_mode="HTML",
        )
        sent, failures = await send_project_outputs(
            context.bot,
            int(update.effective_chat.id),
            project,
            include_all_video=include_all,
        )
        text = f"✅ Отправлено файлов: {sent}." if sent else "⚠️ Готовые файлы пока не найдены."
        if failures:
            text += "\n\n⚠️ Не отправились:\n" + "\n".join(f"• {html.escape(item)}" for item in failures[:8])
        try:
            await status.edit_text(text, parse_mode="HTML")
        except Exception:
            await update.effective_message.reply_text(text, parse_mode="HTML")
    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Отправка: " + html.escape(_short(str(exc), 900)),
            parse_mode="HTML",
        )


def register_dub_delivery_handlers(application: Any) -> None:
    if application.bot_data.get("dub_studio_delivery_registered"):
        return
    application.add_handler(CommandHandler("dubsend", dubsend_command, filters=_MSG_ONLY))
    application.bot_data["dub_studio_delivery_registered"] = True


__all__ = [
    "available_outputs",
    "dubsend_command",
    "register_dub_delivery_handlers",
    "send_project_outputs",
]
