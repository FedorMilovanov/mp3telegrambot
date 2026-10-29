#!/usr/bin/env python3
"""Delivery commands and automatic result sender for Dub Studio projects."""

from __future__ import annotations

import html
from typing import Any

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, filters

from core.database import ADMIN_IDS
from services.dub_delivery import (
    _dynamic_outputs as _dynamic_outputs,
    _dynamic_project_root as _dynamic_project_root,
    _recipe_outputs as _recipe_outputs,
    _short as _short,
    available_outputs as available_outputs,
    send_project_outputs,
)
from services.dub_studio import DubStore

_MSG_ONLY = filters.UpdateType.MESSAGE


async def _admin(update: Update) -> bool:
    user = update.effective_user
    if user and user.id in ADMIN_IDS:
        return True
    if update.effective_message:
        await update.effective_message.reply_text("⛔ Отправка Dub Studio доступна только администратору.")
    return False


def _resolve_project(store: DubStore, user_id: int, token: str | None) -> dict[str, Any]:
    token = str(token or "").strip().lower()
    project: dict[str, Any] | None
    if token and token not in {"last", "последний", "all", "все"}:
        project = store.get_project(token)
    else:
        project = store.latest_project(owner_user_id=user_id)
    if not project:
        raise KeyError("У вас ещё нет Dub Studio проектов.")
    if int(project["owner_user_id"]) != int(user_id):
        raise PermissionError("Это не ваш проект.")
    return project


async def dubsend_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    user = update.effective_user
    message = update.effective_message
    chat = update.effective_chat
    if user is None or message is None or chat is None:
        return
    args = list(context.args or [])
    include_all = any(str(arg).strip().lower() in {"all", "все"} for arg in args)
    token = next((arg for arg in args if str(arg).strip().lower() not in {"all", "все"}), None)
    try:
        project = _resolve_project(DubStore(), user.id, token)
        status = await message.reply_text(
            f"📤 Отправляю результаты проекта <code>{html.escape(str(project['id']))}</code>…",
            parse_mode="HTML",
        )
        sent, failures = await send_project_outputs(
            context.bot,
            int(chat.id),
            project,
            include_all_video=include_all,
        )
        text = f"✅ Отправлено файлов: {sent}." if sent else "⚠️ Готовые файлы пока не найдены."
        if failures:
            text += "\n\n⚠️ Не отправились:\n" + "\n".join(f"• {html.escape(item)}" for item in failures[:8])
        try:
            await status.edit_text(text, parse_mode="HTML")
        except Exception:
            await message.reply_text(text, parse_mode="HTML")
    except Exception as exc:
        await message.reply_text(
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
