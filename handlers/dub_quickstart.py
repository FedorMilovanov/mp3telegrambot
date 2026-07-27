#!/usr/bin/env python3
"""One-command creation and queueing for Dub Studio recipes."""
from __future__ import annotations

import html
from typing import Any

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, filters

from core.database import ADMIN_IDS
from services.dub_studio import DubStore, list_recipes

_MSG_ONLY = filters.UpdateType.MESSAGE


def _recipe_help() -> str:
    recipes = list_recipes()
    if not recipes:
        return "Рецепты пока не зарегистрированы."
    return "\n".join(
        f"• <code>{html.escape(recipe.recipe_id)}</code> — {html.escape(recipe.title[:150])}"
        for recipe in recipes
    )


async def dubstart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or user.id not in ADMIN_IDS:
        if message:
            await message.reply_text("⛔ Dub Studio доступна только администратору.")
        return
    args = list(context.args or [])
    if not args:
        await message.reply_text(
            "Использование: <code>/dubstart RECIPE</code>\n\n" + _recipe_help(),
            parse_mode="HTML",
        )
        return
    recipe_id = str(args[0]).strip().lower()
    try:
        store = DubStore()
        project = store.create_project(
            recipe_id,
            owner_user_id=user.id,
            owner_chat_id=update.effective_chat.id,
        )
        job = store.enqueue_job(str(project["id"]), "render")
        await message.reply_text(
            "🚀 <b>Dub Studio: production запущен</b>\n\n"
            f"Проект: <code>{html.escape(str(project['id']))}</code>\n"
            f"Рецепт: <code>{html.escape(recipe_id)}</code>\n"
            f"Задание: <b>#{job['id']}</b>\n\n"
            f"Статус: <code>/dubstatus {html.escape(str(project['id']))}</code>\n"
            f"После завершения: <code>/dubsend {html.escape(str(project['id']))}</code>",
            parse_mode="HTML",
        )
    except Exception as exc:
        await message.reply_text(
            "❌ Не удалось запустить Dub Studio: " + html.escape(str(exc)[:900]),
            parse_mode="HTML",
        )


def register_dub_quickstart_handler(application: Any) -> None:
    if application.bot_data.get("dub_studio_quickstart_registered"):
        return
    application.add_handler(CommandHandler("dubstart", dubstart_command, filters=_MSG_ONLY))
    application.bot_data["dub_studio_quickstart_registered"] = True


__all__ = ["dubstart_command", "register_dub_quickstart_handler"]
