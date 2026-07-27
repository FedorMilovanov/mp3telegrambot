#!/usr/bin/env python3
"""Menu-driven universal Dub Studio wizard."""
from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from core.database import ADMIN_IDS
from services.dub_studio import DubStore, studio_root
from tools.voxcpm2.generic_project_runtime import parse_custom_translation

_GENERIC_RECIPE = "generic_short_v1"
_WIZARD_KEY = "dub_universal_wizard"
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
_MSG_ONLY = filters.UpdateType.MESSAGE


def _short(value: str, limit: int = 300) -> str:
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[: max(1, limit - 1)].rstrip() + "…"


def _project_root(project_id: str) -> Path:
    root = (studio_root() / "projects" / project_id).resolve()
    allowed = (studio_root() / "projects").resolve()
    root.relative_to(allowed)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _extract_youtube_video_id(value: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = parsed.netloc.casefold().split(":", 1)[0]
    if host not in _YOUTUBE_HOSTS:
        raise ValueError("Нужна ссылка YouTube или YouTube Shorts.")
    video_id = ""
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
    else:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
            video_id = parts[1]
        else:
            video_id = (parse_qs(parsed.query).get("v") or [""])[0]
    video_id = video_id.split("?", 1)[0].split("&", 1)[0]
    if not _VIDEO_ID_RE.fullmatch(video_id):
        raise ValueError("Не удалось определить YouTube video ID из ссылки.")
    canonical = f"https://youtube.com/watch?v={video_id}"
    return video_id, canonical


def _home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Новый ролик — Gemini MAX", callback_data="dubwiz|mode|gemini")],
        [InlineKeyboardButton("✍️ Новый ролик — мой перевод", callback_data="dubwiz|mode|custom")],
        [
            InlineKeyboardButton("📂 Мои проекты", callback_data="dubwiz|projects|list"),
            InlineKeyboardButton("⚙️ Worker", callback_data="dubwiz|worker|status"),
        ],
    ])


def _mode_text(mode: str) -> str:
    if mode == "gemini":
        return (
            "🤖 <b>Gemini MAX</b>\n\n"
            "Бот предпочитает ручные субтитры автора, затем автоматические YouTube captions, "
            "и только при их отсутствии запускает Whisper. Перевод проходит три независимых "
            "редакторских этапа на максимальной модели.\n\n"
            "Пришлите ссылку YouTube."
        )
    return (
        "✍️ <b>Свой перевод</b>\n\n"
        "Бот сначала скачает ролик, найдёт лучшие доступные субтитры и пришлёт TXT-шаблон "
        "с точной расшифровкой, ID и таймкодами. Вы сможете исследовать контекст и заполнить "
        "строки RU:. Ваш текст не будет переписан Gemini.\n\n"
        "Пришлите ссылку YouTube."
    )


def _request_payload(video_id: str, url: str, mode: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "video_id": video_id,
        "source_url": url,
        "translation_mode": mode,
        "original_level": 0.18,
        "russian_delay_ms": 420,
        "threads": int(os.getenv("DUB_VOX_THREADS", "10")),
        "steps": int(os.getenv("DUB_VOX_STEPS", "16")),
        "cfg": float(os.getenv("DUB_VOX_CFG", "1.8")),
        "whisper_model": os.getenv("DUB_WHISPER_MODEL", "large-v3"),
        "translation_model": os.getenv("DUB_TRANSLATION_MODEL", "gemini-3.6-flash"),
        "title_model": os.getenv("DUB_TITLE_MODEL", "gemini-3.5-flash-lite"),
        "vox_archive": os.getenv("DUB_VOX_ARCHIVE", r"C:\AI-Archive\VoxCPM2-paused-RTX3060"),
        "cpu_venv": os.getenv("DUB_CPU_VENV", r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv"),
    }


async def _admin(update: Update) -> bool:
    user = update.effective_user
    if user and user.id in ADMIN_IDS:
        return True
    message = update.effective_message
    if message:
        await message.reply_text("⛔ Универсальный Dub Studio доступен только администратору.")
    return False


async def dub_home_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    if context.args:
        from handlers.dub_commands import dub_command

        await dub_command(update, context)
        return
    context.user_data.pop(_WIZARD_KEY, None)
    await update.effective_message.reply_text(
        "🎙 <b>Dub Studio — новый ролик под ключ</b>\n\n"
        "Выберите источник перевода. Для любого нового ролика будут автоматически подобраны "
        "субтитры, голосовые референсы, тайминги, русское название файла и параметры мастера.\n\n"
        "Звук по умолчанию: оригинал <b>18%</b>, русский голос с задержкой <b>420 мс</b>.",
        parse_mode="HTML",
        reply_markup=_home_keyboard(),
    )


async def dubnewvideo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await dub_home_command(update, context)


async def _show_projects(update: Update) -> None:
    projects = DubStore().list_projects(owner_user_id=update.effective_user.id, limit=10)
    lines = ["📂 <b>Последние проекты</b>", ""]
    if not projects:
        lines.append("Проектов пока нет.")
    for project in projects:
        lines.append(
            f"• <code>{html.escape(str(project['id']))}</code> — "
            f"{html.escape(_short(str(project['title']), 80))} · "
            f"<b>{html.escape(str(project['status']))}</b> {int(project.get('progress') or 0)}%"
        )
    lines.extend(["", "Статус: <code>/dubstatus ID</code>", "Получить файлы: <code>/dubsend ID</code>"])
    target = update.callback_query
    if target:
        await target.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=_home_keyboard())
    else:
        await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=_home_keyboard())


async def handle_dub_wizard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not await _admin(update):
        return
    parts = str(query.data or "").split("|", 2)
    if len(parts) != 3 or parts[0] != "dubwiz":
        return
    action, value = parts[1], parts[2]
    if action == "mode" and value in {"gemini", "custom"}:
        context.user_data[_WIZARD_KEY] = {"awaiting": "url", "mode": value}
        await query.edit_message_text(
            _mode_text(value),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="dubwiz|home|show")]]),
        )
        return
    if action == "home":
        context.user_data.pop(_WIZARD_KEY, None)
        await query.edit_message_text(
            "🎙 <b>Dub Studio — новый ролик под ключ</b>\n\nВыберите режим перевода.",
            parse_mode="HTML",
            reply_markup=_home_keyboard(),
        )
        return
    if action == "projects":
        await _show_projects(update)
        return
    if action == "worker":
        from handlers.dub_commands import dubworker_command

        await dubworker_command(update, context)
        return
    if action == "translation":
        await _begin_translation_upload(update, context, value)


async def _create_generic_project(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, mode: str) -> None:
    video_id, canonical_url = _extract_youtube_video_id(url)
    store = DubStore()
    project = store.create_project(
        _GENERIC_RECIPE,
        owner_user_id=update.effective_user.id,
        owner_chat_id=update.effective_chat.id,
        title=f"Видео {video_id} — {'Gemini MAX' if mode == 'gemini' else 'свой перевод'}",
        metadata={"video_id": video_id, "translation_mode": mode},
    )
    root = _project_root(str(project["id"]))
    request = _request_payload(video_id, canonical_url, mode)
    (root / "request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    action = "render_gemini" if mode == "gemini" else "prepare_custom"
    job = store.enqueue_job(str(project["id"]), action)
    context.user_data.pop(_WIZARD_KEY, None)
    details = (
        "Бот сам подготовит перевод и готовый ролик."
        if mode == "gemini"
        else "Сначала бот пришлёт точную расшифровку и шаблон для вашего перевода."
    )
    await update.effective_message.reply_text(
        "🚀 <b>Проект создан</b>\n\n"
        f"ID: <code>{html.escape(str(project['id']))}</code>\n"
        f"Видео: <code>{html.escape(video_id)}</code>\n"
        f"Режим: <b>{'Gemini MAX' if mode == 'gemini' else 'свой перевод'}</b>\n"
        f"Задание: <b>#{job['id']}</b>\n\n"
        f"{details}\n\n"
        f"Статус: <code>/dubstatus {html.escape(str(project['id']))}</code>",
        parse_mode="HTML",
    )


async def _begin_translation_upload(update: Update, context: ContextTypes.DEFAULT_TYPE, project_id: str) -> None:
    store = DubStore()
    project = store.get_project(project_id)
    if int(project["owner_user_id"]) != int(update.effective_user.id):
        raise PermissionError("Это не ваш проект.")
    root = _project_root(project_id)
    template = root / "output" / "translation_template.txt"
    groups = root / "source_groups.json"
    if not template.is_file() or not groups.is_file():
        raise RuntimeError("Шаблон ещё не готов. Дождитесь завершения этапа подготовки.")
    context.user_data[_WIZARD_KEY] = {"awaiting": "translation", "project_id": project_id}
    message = update.effective_message
    with template.open("rb") as handle:
        await message.reply_document(
            document=handle,
            filename=template.name,
            caption=(
                "Заполните строки RU: и пришлите этот TXT обратно. Также можно отправить текст сообщением. "
                "ID и количество блоков должны сохраниться."
            ),
            read_timeout=300,
            write_timeout=300,
        )


async def dubtranslation_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    token = str((context.args or [""])[0]).strip().lower()
    project = DubStore().get_project(token) if token else DubStore().latest_project(owner_user_id=update.effective_user.id)
    if not project:
        await update.effective_message.reply_text("Проект не найден.")
        return
    try:
        await _begin_translation_upload(update, context, str(project["id"]))
    except Exception as exc:
        await update.effective_message.reply_text("⚠️ " + html.escape(_short(str(exc), 900)), parse_mode="HTML")


def _read_uploaded_text(update: Update, raw_text: str | None = None) -> str:
    if raw_text is not None:
        return raw_text
    raise RuntimeError("Внутренняя ошибка чтения перевода.")


async def _store_custom_translation(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    state = context.user_data.get(_WIZARD_KEY) or {}
    project_id = str(state.get("project_id") or "")
    if state.get("awaiting") != "translation" or not project_id:
        return
    root = _project_root(project_id)
    groups_path = root / "source_groups.json"
    if not groups_path.is_file():
        raise RuntimeError("Не найдены исходные блоки проекта.")
    groups = json.loads(groups_path.read_text(encoding="utf-8-sig"))
    translations = parse_custom_translation(text, groups)
    input_dir = root / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "custom_translation.txt").write_text(text.rstrip() + "\n", encoding="utf-8")
    (input_dir / "custom_translation.json").write_text(
        json.dumps({"segments": translations}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    job = DubStore().enqueue_job(project_id, "render_custom")
    context.user_data.pop(_WIZARD_KEY, None)
    await update.effective_message.reply_text(
        "✅ <b>Ваш перевод принят без переписывания</b>\n\n"
        f"Блоков: <b>{len(translations)}</b>\n"
        f"Рендер: задание <b>#{job['id']}</b>\n"
        "Перед VoxCPM2 будет выполнена строгая проверка произносимой длины. "
        "Если какой-то блок физически не помещается, бот укажет конкретные ID.",
        parse_mode="HTML",
    )


async def handle_dub_wizard_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get(_WIZARD_KEY) or {}
    if not state:
        return
    if not await _admin(update):
        return
    awaiting = state.get("awaiting")
    text = str(update.effective_message.text or "").strip()
    if awaiting == "url":
        try:
            await _create_generic_project(update, context, text, str(state.get("mode") or ""))
        except Exception as exc:
            await update.effective_message.reply_text(
                "⚠️ " + html.escape(_short(str(exc), 900)) + "\n\nПришлите корректную ссылку YouTube.",
                parse_mode="HTML",
            )
        raise ApplicationHandlerStop
    if awaiting == "translation":
        try:
            await _store_custom_translation(update, context, _read_uploaded_text(update, text))
        except Exception as exc:
            await update.effective_message.reply_text(
                "⚠️ Перевод не принят: " + html.escape(_short(str(exc), 1600)),
                parse_mode="HTML",
            )
        raise ApplicationHandlerStop


async def handle_dub_wizard_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get(_WIZARD_KEY) or {}
    if state.get("awaiting") != "translation":
        return
    if not await _admin(update):
        return
    document = update.effective_message.document
    if not document:
        return
    suffix = Path(document.file_name or "translation.txt").suffix.casefold()
    if suffix not in {".txt", ".md", ".json"}:
        await update.effective_message.reply_text("Нужен файл TXT, MD или JSON.")
        return
    try:
        telegram_file = await context.bot.get_file(document.file_id)
        payload = await telegram_file.download_as_bytearray()
        if len(payload) > 2_000_000:
            raise RuntimeError("Файл перевода слишком большой.")
        text = bytes(payload).decode("utf-8-sig", errors="strict")
        await _store_custom_translation(update, context, text)
    except Exception as exc:
        await update.effective_message.reply_text(
            "⚠️ Файл перевода не принят: " + html.escape(_short(str(exc), 1600)),
            parse_mode="HTML",
        )
    raise ApplicationHandlerStop


def register_dub_wizard_handlers(application: Any) -> None:
    if application.bot_data.get("dub_studio_wizard_registered"):
        return
    application.add_handler(CommandHandler("dub", dub_home_command, filters=_MSG_ONLY), group=0)
    application.add_handler(CommandHandler("dubnewvideo", dubnewvideo_command, filters=_MSG_ONLY), group=-60)
    application.add_handler(CommandHandler("dubtranslation", dubtranslation_command, filters=_MSG_ONLY), group=-60)
    application.add_handler(CallbackQueryHandler(handle_dub_wizard_callback, pattern=r"^dubwiz\|"), group=-60)
    application.add_handler(
        MessageHandler(filters.Document.ALL, handle_dub_wizard_document),
        group=-59,
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_dub_wizard_text),
        group=-59,
    )
    application.bot_data["dub_studio_wizard_registered"] = True


__all__ = [
    "_extract_youtube_video_id",
    "dub_home_command",
    "dubtranslation_command",
    "handle_dub_wizard_callback",
    "handle_dub_wizard_document",
    "handle_dub_wizard_text",
    "register_dub_wizard_handlers",
]
