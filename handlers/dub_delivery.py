#!/usr/bin/env python3
"""Delivery commands for completed Dub Studio projects."""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, filters

from core.database import ADMIN_IDS
from services.dub_studio import DubStore, load_recipe, resolve_recipe_path

_MSG_ONLY = filters.UpdateType.MESSAGE
_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}
_DOCUMENT_SUFFIXES = {".srt", ".ass", ".txt", ".json", ".wav"}


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
        return store.get_project(token)
    project = store.latest_project(owner_user_id=user_id)
    if not project:
        raise KeyError("У вас ещё нет Dub Studio проектов.")
    return project


def available_outputs(project: dict[str, Any], *, include_all_video: bool = False) -> list[dict[str, Any]]:
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
        rows.append(
            {
                "name": str(name),
                "label": str(spec.get("label") or name),
                "path": path,
                "primary": is_primary,
                "video": is_video,
            }
        )
    rows.sort(key=lambda item: (not item["primary"], not item["video"], item["name"]))
    return rows


async def dubsend_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    args = list(context.args or [])
    include_all = any(str(arg).strip().lower() in {"all", "все"} for arg in args)
    token = next((arg for arg in args if str(arg).strip().lower() not in {"all", "все"}), None)
    try:
        store = DubStore()
        project = _resolve_project(store, update.effective_user.id, token)
        outputs = available_outputs(project, include_all_video=include_all)
        if not outputs:
            await update.effective_message.reply_text(
                "⚠️ Готовых файлов пока нет. Проверьте <code>/dubstatus</code> или <code>/dubfiles</code>.",
                parse_mode="HTML",
            )
            return

        status = await update.effective_message.reply_text(
            f"📤 Отправляю результаты проекта <code>{html.escape(str(project['id']))}</code>…",
            parse_mode="HTML",
        )
        sent = 0
        failures: list[str] = []
        for item in outputs:
            path: Path = item["path"]
            label = _short(str(item["label"]), 800)
            try:
                with path.open("rb") as handle:
                    if item["video"]:
                        await update.effective_message.reply_video(
                            video=handle,
                            filename=path.name,
                            caption=label,
                            supports_streaming=True,
                            write_timeout=1800,
                            read_timeout=1800,
                            connect_timeout=120,
                            pool_timeout=120,
                        )
                    else:
                        await update.effective_message.reply_document(
                            document=handle,
                            filename=path.name,
                            caption=label,
                            write_timeout=1800,
                            read_timeout=1800,
                            connect_timeout=120,
                            pool_timeout=120,
                        )
                sent += 1
            except Exception as exc:
                failures.append(f"{path.name}: {_short(str(exc), 180)}")

        text = f"✅ Отправлено файлов: {sent}."
        if failures:
            text += "\n\n⚠️ Не отправились:\n" + "\n".join(
                f"• {html.escape(item)}" for item in failures[:8]
            )
            text += "\n\nПути остаются доступны через <code>/dubfiles</code>."
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


__all__ = ["available_outputs", "dubsend_command", "register_dub_delivery_handlers"]
