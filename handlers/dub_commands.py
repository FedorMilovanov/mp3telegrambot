#!/usr/bin/env python3
"""Telegram admin control surface for local VoxCPM2 Dub Studio."""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, filters

from core.database import ADMIN_IDS
from handlers.dub_delivery import available_outputs
from services.dub_studio import DubStore, list_recipes, load_recipe, worker_is_fresh

_STATUS_ICON = {
    "draft": "📝",
    "queued": "🕓",
    "rendering": "⚙️",
    "cancelling": "🛑",
    "cancelled": "🚫",
    "done": "✅",
    "failed": "❌",
}
_MSG_ONLY = filters.UpdateType.MESSAGE
_GENERIC_RECIPE = "generic_short_v1"


async def _admin(update: Update) -> bool:
    user = update.effective_user
    if user and user.id in ADMIN_IDS:
        return True
    if update.effective_message:
        await update.effective_message.reply_text(
            f"⛔ Dub Studio доступна только администратору.\n"
            f"Ваш Telegram ID: <code>{getattr(user, 'id', '')}</code>",
            parse_mode="HTML",
        )
    return False


def _store() -> DubStore:
    return DubStore()


def _short(value: str, limit: int = 120) -> str:
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[: max(1, limit - 1)].rstrip() + "…"


def _mode(project: dict[str, Any]) -> str:
    return str((project.get("metadata") or {}).get("translation_mode") or "")


def _default_render_action(project: dict[str, Any]) -> str:
    if str(project.get("recipe_id")) != _GENERIC_RECIPE:
        return "render"
    return "render_direct" if _mode(project) == "direct" else "render_gemini"


def _direct_srt_path(project: dict[str, Any]) -> Path:
    from services.dub_studio import studio_root

    return (
        studio_root()
        / "projects"
        / str(project["id"])
        / "input"
        / "ready_translation.srt"
    ).resolve()


def _render_label(project: dict[str, Any]) -> str:
    return "Повторить SRT-рендер" if _mode(project) == "direct" else "Повторить Gemini"


def _project_owned_by(project: dict[str, Any], user_id: int) -> bool:
    return int(project.get("owner_user_id") or 0) == int(user_id)


def _resolve_project(store: DubStore, user_id: int, token: str | None) -> dict[str, Any]:
    token = str(token or "").strip().lower()
    if token and token not in {"last", "последний"}:
        project = store.get_project(token)
    else:
        project = store.latest_project(owner_user_id=user_id)
    if not project:
        raise KeyError("У вас ещё нет Dub Studio проектов.")
    if not _project_owned_by(project, user_id):
        raise PermissionError("Это не ваш проект.")
    return project


def _project_card(
    project: dict[str, Any],
    *,
    include_jobs: bool = True,
) -> tuple[str, InlineKeyboardMarkup]:
    store = _store()
    recipe = load_recipe(str(project["recipe_id"]))
    jobs = store.recent_jobs(str(project["id"]), limit=3) if include_jobs else []
    icon = _STATUS_ICON.get(str(project["status"]), "ℹ️")
    mode = _mode(project)
    mode_label = "Gemini MAX" if mode == "gemini" else "мой готовый SRT" if mode == "direct" else "recipe"
    lines = [
        f"{icon} <b>{html.escape(_short(str(project['title']), 180))}</b>",
        f"<code>{html.escape(str(project['id']))}</code>",
        "",
        f"Статус: <b>{html.escape(str(project['status']))}</b>",
        f"Этап: <code>{html.escape(_short(str(project['stage']), 100))}</code>",
        f"Прогресс: <b>{int(project.get('progress') or 0)}%</b>",
        f"Рецепт: <code>{html.escape(recipe.recipe_id)}</code>",
    ]
    if str(project.get("recipe_id")) == _GENERIC_RECIPE:
        lines.append(f"Режим: <b>{html.escape(mode_label)}</b>")
        if mode == "direct" and not _direct_srt_path(project).is_file():
            lines.extend(
                [
                    "",
                    "📎 Ожидается готовый русский SRT.",
                    f"Загрузка: <code>/dubsrt {html.escape(str(project['id']))}</code>",
                ]
            )
    if recipe.speaker:
        lines.append(f"Голос: {html.escape(_short(recipe.speaker, 100))}")
    if project.get("last_error"):
        lines.extend(["", "⚠️ " + html.escape(_short(str(project["last_error"]), 600))])
    if jobs:
        lines.extend(["", "<b>Последние задания:</b>"])
        for job in jobs:
            lines.append(
                f"• #{job['id']} <code>{html.escape(str(job['action']))}</code> — "
                f"{html.escape(str(job['status']))} ({int(job.get('progress') or 0)}%)"
            )

    project_id = str(project["id"])
    first_row = [InlineKeyboardButton("🔄 Обновить", callback_data=f"dub|status|{project_id}")]
    can_render = str(project["status"]) not in {"queued", "rendering", "cancelling"}
    if mode == "direct" and not _direct_srt_path(project).is_file():
        first_row.append(
            InlineKeyboardButton("📎 Загрузить SRT", callback_data=f"dubwiz|srt|{project_id}")
        )
    elif can_render:
        action = _default_render_action(project)
        first_row.append(
            InlineKeyboardButton(
                f"▶️ {_render_label(project)}",
                callback_data=f"dub|run:{action}|{project_id}",
            )
        )

    rows: list[list[InlineKeyboardButton]] = [first_row]
    repairs = recipe.repair_actions()
    if repairs:
        rows.append(
            [
                InlineKeyboardButton("🩹 Точечный ремонт", callback_data=f"dub|repairs|{project_id}"),
                InlineKeyboardButton("📦 Файлы", callback_data=f"dub|files|{project_id}"),
            ]
        )
    else:
        rows.append([InlineKeyboardButton("📦 Файлы", callback_data=f"dub|files|{project_id}")])
    if str(project["status"]) in {"queued", "rendering", "cancelling"}:
        rows.append([InlineKeyboardButton("🛑 Отменить", callback_data=f"dub|cancel|{project_id}")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _recipe_text() -> str:
    recipes = list_recipes()
    if not recipes:
        return "Рецепты пока не зарегистрированы."
    lines = ["<b>Доступные production-рецепты:</b>"]
    for recipe in recipes:
        lines.append(
            f"• <code>{html.escape(recipe.recipe_id)}</code> — "
            f"{html.escape(_short(recipe.title, 150))}"
        )
    return "\n".join(lines)


async def dub_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    args = list(context.args or [])
    if args:
        sub = args.pop(0).lower()
        context.args = args
        dispatch = {
            "new": dubnew_command,
            "list": dublist_command,
            "status": dubstatus_command,
            "run": dubrun_command,
            "repair": dubrepair_command,
            "cancel": dubcancel_command,
            "files": dubfiles_command,
            "worker": dubworker_command,
        }
        handler = dispatch.get(sub)
        if handler:
            await handler(update, context)
            return

    worker = _store().latest_worker()
    worker_line = (
        f"✅ worker <code>{html.escape(str(worker['worker_id']))}</code> — "
        f"{html.escape(str(worker['status']))}"
        if worker_is_fresh(worker)
        else "❌ локальный worker не отвечает"
    )
    text = (
        "🎙 <b>VoxCPM2 Dub Studio</b>\n\n"
        "Главное меню новых роликов: <code>/dub</code> без аргументов.\n"
        "Тяжёлый CPU-рендер выполняет отдельный worker; бот остаётся отзывчивым.\n\n"
        f"Worker: {worker_line}\n\n"
        f"{_recipe_text()}\n\n"
        "<b>Служебные команды:</b>\n"
        "<code>/dublist</code> — проекты\n"
        "<code>/dubstatus [ID]</code> — карточка проекта\n"
        "<code>/dubrun [ID]</code> — повторить правильный режим проекта\n"
        "<code>/dubsrt [ID]</code> — загрузить готовый SRT\n"
        "<code>/dubfiles [ID]</code> — результаты\n"
        "<code>/dubcancel [ID]</code> — остановить задание\n"
        "<code>/dubworker</code> — состояние worker"
    )
    await update.effective_message.reply_text(text, parse_mode="HTML")


async def dubnew_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    args = list(context.args or [])
    if not args:
        await update.effective_message.reply_text(
            f"Использование: <code>/dubnew RECIPE</code>\n\n{_recipe_text()}",
            parse_mode="HTML",
        )
        return
    try:
        project = _store().create_project(
            args[0].strip().lower(),
            owner_user_id=update.effective_user.id,
            owner_chat_id=update.effective_chat.id,
        )
        text, keyboard = _project_card(project)
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Не удалось создать проект: " + html.escape(_short(str(exc), 800)),
            parse_mode="HTML",
        )


async def dublist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    projects = _store().list_projects(owner_user_id=update.effective_user.id, limit=10)
    if not projects:
        await update.effective_message.reply_text(
            "Проектов пока нет. Откройте <code>/dub</code>.", parse_mode="HTML"
        )
        return
    lines = ["🎙 <b>Последние Dub Studio проекты</b>", ""]
    for project in projects:
        icon = _STATUS_ICON.get(str(project["status"]), "ℹ️")
        lines.append(
            f"{icon} <code>{html.escape(str(project['id']))}</code> — "
            f"{html.escape(_short(str(project['title']), 120))} · "
            f"{html.escape(str(project['status']))} {int(project.get('progress') or 0)}%"
        )
    lines.extend(["", "Открыть: <code>/dubstatus ID</code>"])
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def dubstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    try:
        project = _resolve_project(_store(), update.effective_user.id, (context.args or [None])[0])
        text, keyboard = _project_card(project)
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as exc:
        await update.effective_message.reply_text(
            "⚠️ " + html.escape(_short(str(exc), 800)), parse_mode="HTML"
        )


async def _enqueue(update: Update, project: dict[str, Any], action: str) -> None:
    try:
        job = _store().enqueue_job(str(project["id"]), action)
        await update.effective_message.reply_text(
            f"🕓 Задание <b>#{job['id']}</b> поставлено в очередь.\n"
            f"Проект: <code>{html.escape(str(project['id']))}</code>\n"
            f"Action: <code>{html.escape(action)}</code>\n\n"
            "Worker продолжит с checkpoint-ов; готовые сегменты повторно не считаются.",
            parse_mode="HTML",
        )
    except Exception as exc:
        await update.effective_message.reply_text(
            "❌ Очередь: " + html.escape(_short(str(exc), 800)), parse_mode="HTML"
        )


async def dubrun_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    try:
        project = _resolve_project(_store(), update.effective_user.id, (context.args or [None])[0])
        if _mode(project) == "direct" and not _direct_srt_path(project).is_file():
            raise RuntimeError(f"Сначала загрузите готовый SRT: /dubsrt {project['id']}")
        await _enqueue(update, project, _default_render_action(project))
    except Exception as exc:
        await update.effective_message.reply_text(
            "⚠️ " + html.escape(_short(str(exc), 800)), parse_mode="HTML"
        )


async def dubrepair_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    args = list(context.args or [])
    try:
        project = _resolve_project(_store(), update.effective_user.id, args[0] if args else None)
        recipe = load_recipe(str(project["recipe_id"]))
        repairs = recipe.repair_actions()
        requested = args[1].lower() if len(args) > 1 else ""
        if requested:
            if requested not in repairs:
                raise ValueError(
                    f"Недоступный repair action {requested}. Доступно: {', '.join(repairs)}"
                )
            await _enqueue(update, project, requested)
            return
        if len(repairs) == 1:
            await _enqueue(update, project, repairs[0])
            return
        if not repairs:
            raise RuntimeError("У этого проекта нет точечных repair-actions.")
        rows = [
            [
                InlineKeyboardButton(
                    str(recipe.actions[name].get("label") or name)[:40],
                    callback_data=f"dub|repair:{name}|{project['id']}",
                )
            ]
            for name in repairs
        ]
        await update.effective_message.reply_text(
            "Выберите точечный ремонт:", reply_markup=InlineKeyboardMarkup(rows)
        )
    except Exception as exc:
        await update.effective_message.reply_text(
            "⚠️ " + html.escape(_short(str(exc), 900)), parse_mode="HTML"
        )


async def dubcancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    try:
        project = _resolve_project(_store(), update.effective_user.id, (context.args or [None])[0])
        job = _store().request_cancel(str(project["id"]))
        await update.effective_message.reply_text(
            f"🛑 Запрос остановки принят для задания #{job['id']}."
        )
    except Exception as exc:
        await update.effective_message.reply_text(
            "⚠️ " + html.escape(_short(str(exc), 800)), parse_mode="HTML"
        )


def _outputs_text(project: dict[str, Any]) -> str:
    lines = [f"📦 <b>Файлы проекта {html.escape(str(project['id']))}</b>", ""]
    outputs = available_outputs(project, include_all_video=True)
    if not outputs:
        lines.extend(
            [
                "Готовых файлов пока нет.",
                f"Статус: <code>/dubstatus {html.escape(str(project['id']))}</code>",
            ]
        )
        return "\n".join(lines)

    for item in outputs:
        path: Path = item["path"]
        size = path.stat().st_size if path.is_file() else 0
        size_text = f" · {size / (1024 * 1024):.1f} МБ" if size else ""
        lines.append(
            f"✅ <b>{html.escape(_short(str(item['label']), 100))}</b>{size_text}\n"
            f"<code>{html.escape(str(path))}</code>"
        )
    lines.extend(
        [
            "",
            f"Отправить: <code>/dubsend {html.escape(str(project['id']))}</code>",
            f"Все видео: <code>/dubsend {html.escape(str(project['id']))} all</code>",
        ]
    )
    return "\n".join(lines)


async def dubfiles_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    try:
        project = _resolve_project(_store(), update.effective_user.id, (context.args or [None])[0])
        await update.effective_message.reply_text(_outputs_text(project), parse_mode="HTML")
    except Exception as exc:
        await update.effective_message.reply_text(
            "⚠️ " + html.escape(_short(str(exc), 800)), parse_mode="HTML"
        )


async def dubworker_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    worker = _store().latest_worker()
    if not worker:
        text = (
            "❌ Worker ещё не регистрировался.\n\n"
            "Проверьте <code>DUB_STUDIO_ENABLED=1</code> и "
            "<code>DUB_STUDIO_AUTOSTART_WORKER=1</code>."
        )
    else:
        fresh = worker_is_fresh(worker)
        details = worker.get("details") or {}
        text = (
            f"{'✅' if fresh else '❌'} <b>Dub Studio worker</b>\n\n"
            f"ID: <code>{html.escape(str(worker['worker_id']))}</code>\n"
            f"PID: <code>{worker['pid']}</code>\n"
            f"Статус: <b>{html.escape(str(worker['status']))}</b>\n"
            f"Heartbeat: <code>{html.escape(str(worker['heartbeat_at']))}</code>\n"
            f"Текущее задание: <code>{html.escape(str(worker.get('current_job_id') or '—'))}</code>\n"
            f"Этап: <code>{html.escape(_short(str(details.get('stage') or '—'), 120))}</code>\n"
            f"Прогресс: <b>{int(details.get('progress') or 0)}%</b>"
        )
    await update.effective_message.reply_text(text, parse_mode="HTML")


async def handle_dub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not await _admin(update):
        return
    parts = str(query.data or "").split("|", 2)
    if len(parts) != 3 or parts[0] != "dub":
        return
    action, project_id = parts[1], parts[2]

    try:
        store = _store()
        project = store.get_project(project_id)
        if not _project_owned_by(project, update.effective_user.id):
            raise PermissionError("Это не ваш проект.")

        if action == "status":
            text, keyboard = _project_card(project)
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        elif action.startswith("run:"):
            render_action = action.split(":", 1)[1]
            expected = _default_render_action(project)
            if render_action != expected:
                raise RuntimeError(f"Режим проекта изменился: ожидается action {expected}.")
            if _mode(project) == "direct" and not _direct_srt_path(project).is_file():
                raise RuntimeError("Сначала загрузите готовый SRT.")
            job = store.enqueue_job(project_id, render_action)
            await query.edit_message_text(
                f"🕓 Задание #{job['id']} поставлено в очередь.\n"
                f"<code>{html.escape(project_id)}</code>\n"
                f"<code>{html.escape(render_action)}</code>",
                parse_mode="HTML",
            )
        elif action == "run":
            render_action = _default_render_action(project)
            if _mode(project) == "direct" and not _direct_srt_path(project).is_file():
                raise RuntimeError("Сначала загрузите готовый SRT.")
            job = store.enqueue_job(project_id, render_action)
            await query.edit_message_text(
                f"🕓 Задание #{job['id']} поставлено в очередь.\n"
                f"<code>{html.escape(render_action)}</code>",
                parse_mode="HTML",
            )
        elif action == "cancel":
            job = store.request_cancel(project_id)
            await query.edit_message_text(f"🛑 Запрошена остановка задания #{job['id']}.")
        elif action == "files":
            await query.edit_message_text(_outputs_text(project), parse_mode="HTML")
        elif action == "repairs":
            recipe = load_recipe(str(project["recipe_id"]))
            rows = [
                [
                    InlineKeyboardButton(
                        str(recipe.actions[name].get("label") or name)[:40],
                        callback_data=f"dub|repair:{name}|{project_id}",
                    )
                ]
                for name in recipe.repair_actions()
            ]
            await query.edit_message_text(
                "Выберите точечный ремонт:", reply_markup=InlineKeyboardMarkup(rows)
            )
        elif action.startswith("repair:"):
            repair_action = action.split(":", 1)[1]
            recipe = load_recipe(str(project["recipe_id"]))
            if repair_action not in recipe.repair_actions():
                raise ValueError("Недоступный repair action.")
            job = store.enqueue_job(project_id, repair_action)
            await query.edit_message_text(
                f"🩹 Точечный ремонт поставлен в очередь: #{job['id']}\n"
                f"<code>{html.escape(repair_action)}</code>",
                parse_mode="HTML",
            )
    except Exception as exc:
        await query.edit_message_text(
            "❌ " + html.escape(_short(str(exc), 1000)), parse_mode="HTML"
        )


def register_dub_handlers(application: Any) -> None:
    if application.bot_data.get("dub_studio_handlers_registered"):
        return
    application.add_handler(CommandHandler("dubnew", dubnew_command, filters=_MSG_ONLY))
    application.add_handler(CommandHandler("dublist", dublist_command, filters=_MSG_ONLY))
    application.add_handler(CommandHandler("dubstatus", dubstatus_command, filters=_MSG_ONLY))
    application.add_handler(CommandHandler("dubrun", dubrun_command, filters=_MSG_ONLY))
    application.add_handler(CommandHandler("dubrepair", dubrepair_command, filters=_MSG_ONLY))
    application.add_handler(CommandHandler("dubcancel", dubcancel_command, filters=_MSG_ONLY))
    application.add_handler(CommandHandler("dubfiles", dubfiles_command, filters=_MSG_ONLY))
    application.add_handler(CommandHandler("dubworker", dubworker_command, filters=_MSG_ONLY))
    application.add_handler(CallbackQueryHandler(handle_dub_callback, pattern=r"^dub\|"))
    application.bot_data["dub_studio_handlers_registered"] = True


__all__ = [
    "_default_render_action",
    "_outputs_text",
    "register_dub_handlers",
    "dub_command",
    "dubnew_command",
    "dublist_command",
    "dubstatus_command",
    "dubrun_command",
    "dubrepair_command",
    "dubcancel_command",
    "dubfiles_command",
    "dubworker_command",
    "handle_dub_callback",
]
