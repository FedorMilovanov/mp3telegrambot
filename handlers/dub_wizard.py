#!/usr/bin/env python3
"""Menu-driven universal Dub Studio wizard.

Two intentionally separate production modes:
- Gemini MAX: best available source captions -> multi-pass translation -> render.
- Ready SRT: user supplies final Russian SRT -> render verbatim, no Gemini review.
"""
from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.database import ADMIN_IDS
from services.dub_studio import DubStore, studio_root
from tools.voxcpm2.generic_direct_runtime import parse_srt_text

_GENERIC_RECIPE = "generic_short_v1"
_WIZARD_KEY = "dub_universal_wizard"
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
_MSG_ONLY = filters.UpdateType.MESSAGE
_DIRECT_MODE = "direct"
_GEMINI_MODE = "gemini"


def _short(value: str, limit: int = 300) -> str:
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[: max(1, limit - 1)].rstrip() + "…"


def _project_root(project_id: str) -> Path:
    root = (studio_root() / "projects" / str(project_id)).resolve()
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
    return video_id, f"https://youtube.com/watch?v={video_id}"


def _home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🤖 Gemini MAX — полный перевод",
                    callback_data="dubwiz|mode|gemini",
                )
            ],
            [
                InlineKeyboardButton(
                    "✍️ Мой готовый перевод — SRT",
                    callback_data="dubwiz|mode|direct",
                )
            ],
            [
                InlineKeyboardButton("📂 Мои проекты", callback_data="dubwiz|projects|list"),
                InlineKeyboardButton("⚙️ Worker", callback_data="dubwiz|worker|status"),
            ],
            [
                InlineKeyboardButton("🎛 Все режимы бота", callback_data="mode_menu:home")
            ],
        ]
    )


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("↩️ Dub Studio", callback_data="dubwiz|home|show"),
            InlineKeyboardButton("🎛 Все режимы", callback_data="mode_menu:home"),
        ]]
    )


def _srt_keyboard(project_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📎 Загрузить готовый SRT",
                    callback_data=f"dubwiz|srt|{project_id}",
                )
            ],
            [
                InlineKeyboardButton("↩️ Dub Studio", callback_data="dubwiz|home|show"),
                InlineKeyboardButton("🎛 Все режимы", callback_data="mode_menu:home"),
            ],
        ]
    )


def _mode_text(mode: str) -> str:
    if mode == _GEMINI_MODE:
        return (
            "🤖 <b>Gemini MAX — перевод с полной проверкой</b>\n\n"
            "Бот сначала ищет ручные субтитры автора. Если их нет — использует "
            "автоматические captions YouTube, затем Whisper. Русский перевод проходит "
            "три редакторских прохода и отдельную подгонку только перегруженных реплик.\n\n"
            "Пришлите ссылку YouTube. После этого бот сам сделает и отправит готовый ролик."
        )

    return (
        "✍️ <b>Мой готовый перевод — SRT без изменений</b>\n\n"
        "Пришлите ссылку YouTube, затем готовый русский файл <code>.srt</code>. "
        "Русский текст считается окончательным: Gemini его не проверяет, не сокращает "
        "и не переписывает. Бот использует SRT только как текст и таймкоды, озвучивает "
        "его и отправляет готовый ролик.\n\n"
        "Таймкоды ставьте по исходному видео. Дополнительную задержку русского 420 мс "
        "бот добавит сам."
    )


def _request_payload(video_id: str, url: str, mode: str) -> dict[str, Any]:
    if mode not in {_GEMINI_MODE, _DIRECT_MODE}:
        raise ValueError("Неизвестный режим Dub Studio.")
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
        "vox_archive": os.getenv(
            "DUB_VOX_ARCHIVE",
            r"C:\AI-Archive\VoxCPM2-paused-RTX3060",
        ),
        "cpu_venv": os.getenv(
            "DUB_CPU_VENV",
            r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv",
        ),
    }


async def _admin(update: Update) -> bool:
    user = update.effective_user
    if user and user.id in ADMIN_IDS:
        return True
    if update.effective_message:
        await update.effective_message.reply_text(
            "⛔ Универсальный Dub Studio доступен только администратору."
        )
    return False


def _project_owned_by(project: dict[str, Any], user_id: int) -> bool:
    return int(project.get("owner_user_id") or 0) == int(user_id)


def _latest_direct_draft(user_id: int) -> dict[str, Any] | None:
    for project in DubStore().list_projects(owner_user_id=user_id, limit=10):
        metadata = project.get("metadata") or {}
        if (
            str(project.get("recipe_id")) == _GENERIC_RECIPE
            and str(metadata.get("translation_mode")) == _DIRECT_MODE
            and str(project.get("status")) in {"draft", "failed", "cancelled"}
        ):
            ready = _project_root(str(project["id"])) / "input" / "ready_translation.srt"
            if not ready.is_file():
                return project
    return None


async def dub_home_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    if context.args:
        from handlers.dub_commands import dub_command

        await dub_command(update, context)
        return

    context.user_data.pop(_WIZARD_KEY, None)
    await update.effective_message.reply_text(
        "🎙 <b>Dub Studio — ролик под ключ</b>\n\n"
        "Два режима полностью разделены:\n"
        "• <b>Gemini MAX</b> сам получает исходный текст, переводит и проверяет.\n"
        "• <b>Мой готовый SRT</b> озвучивает ваш окончательный русский текст без правок.\n\n"
        "Для обоих режимов: оригинал <b>18%</b>, русский голос с задержкой "
        "<b>420 мс</b>, готовый MP4 приходит в Telegram.",
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
        metadata = project.get("metadata") or {}
        mode = str(metadata.get("translation_mode") or "")
        mode_label = "Gemini MAX" if mode == _GEMINI_MODE else "готовый SRT" if mode == _DIRECT_MODE else "recipe"
        lines.append(
            f"• <code>{html.escape(str(project['id']))}</code> — "
            f"{html.escape(_short(str(project['title']), 72))} · "
            f"<b>{html.escape(mode_label)}</b> · "
            f"{html.escape(str(project['status']))} {int(project.get('progress') or 0)}%"
        )
    lines.extend(
        [
            "",
            "Статус: <code>/dubstatus ID</code>",
            "Загрузить SRT повторно: <code>/dubsrt ID</code>",
            "Получить файлы: <code>/dubsend ID</code>",
        ]
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=_home_keyboard(),
        )
    else:
        await update.effective_message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=_home_keyboard(),
        )


async def _activate_srt_upload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    project_id: str,
) -> None:
    store = DubStore()
    project = store.get_project(project_id)
    if not _project_owned_by(project, update.effective_user.id):
        raise PermissionError("Это не ваш проект.")
    metadata = project.get("metadata") or {}
    if str(metadata.get("translation_mode")) != _DIRECT_MODE:
        raise RuntimeError("Этот проект работает в режиме Gemini MAX и не принимает готовый SRT.")
    if str(project.get("status")) in {"queued", "rendering", "cancelling"}:
        raise RuntimeError("Проект уже выполняется. Сначала дождитесь завершения или отмените job.")

    context.user_data[_WIZARD_KEY] = {
        "awaiting": "srt",
        "mode": _DIRECT_MODE,
        "project_id": project_id,
    }
    message = update.effective_message
    await message.reply_text(
        "📎 <b>Пришлите готовый русский SRT</b>\n\n"
        "Текст будет озвучен без Gemini-проверки и без переписывания. "
        "Допустим файл <code>.srt</code> в UTF-8, UTF-16 или Windows-1251. "
        "Можно также вставить содержимое SRT обычным сообщением.\n\n"
        "Таймкоды — по исходному видео; задержку 420 мс бот добавит сам.",
        parse_mode="HTML",
    )


async def handle_dub_wizard_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
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

    if action == "mode" and value in {_GEMINI_MODE, _DIRECT_MODE}:
        context.user_data[_WIZARD_KEY] = {"awaiting": "url", "mode": value}
        await query.edit_message_text(
            _mode_text(value),
            parse_mode="HTML",
            reply_markup=_back_keyboard(),
        )
        return

    if action == "home":
        context.user_data.pop(_WIZARD_KEY, None)
        await query.edit_message_text(
            "🎙 <b>Dub Studio — ролик под ключ</b>\n\nВыберите режим.",
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

    if action in {"srt", "translation"}:
        try:
            await _activate_srt_upload(update, context, value)
        except Exception as exc:
            await query.message.reply_text(
                "⚠️ " + html.escape(_short(str(exc), 900)),
                parse_mode="HTML",
            )
        return


async def _create_generic_project(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    mode: str,
) -> None:
    video_id, canonical_url = _extract_youtube_video_id(url)
    store = DubStore()
    mode_label = "Gemini MAX" if mode == _GEMINI_MODE else "мой готовый SRT"
    project = store.create_project(
        _GENERIC_RECIPE,
        owner_user_id=update.effective_user.id,
        owner_chat_id=update.effective_chat.id,
        title=f"Видео {video_id} — {mode_label}",
        metadata={"video_id": video_id, "translation_mode": mode},
    )
    project_id = str(project["id"])
    root = _project_root(project_id)
    request = _request_payload(video_id, canonical_url, mode)
    (root / "request.json").write_text(
        json.dumps(request, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if mode == _GEMINI_MODE:
        job = store.enqueue_job(project_id, "render_gemini")
        context.user_data.pop(_WIZARD_KEY, None)
        await update.effective_message.reply_text(
            "🚀 <b>Gemini MAX запущен</b>\n\n"
            f"Проект: <code>{html.escape(project_id)}</code>\n"
            f"Видео: <code>{html.escape(video_id)}</code>\n"
            f"Задание: <b>#{job['id']}</b>\n\n"
            "Бот сам выберет лучший источник субтитров, выполнит многоступенчатый "
            "перевод, озвучит и пришлёт готовый MP4.\n\n"
            f"Статус: <code>/dubstatus {html.escape(project_id)}</code>",
            parse_mode="HTML",
        )
        return

    context.user_data[_WIZARD_KEY] = {
        "awaiting": "srt",
        "mode": _DIRECT_MODE,
        "project_id": project_id,
    }
    await update.effective_message.reply_text(
        "✅ <b>Ссылка принята</b>\n\n"
        f"Проект: <code>{html.escape(project_id)}</code>\n"
        f"Видео: <code>{html.escape(video_id)}</code>\n\n"
        "Теперь пришлите готовый русский файл <code>.srt</code>. "
        "Сразу после загрузки бот поставит озвучивание и сборку ролика в очередь. "
        "Никакой расшифровки, шаблонов и проверки перевода Gemini не будет.",
        parse_mode="HTML",
        reply_markup=_srt_keyboard(project_id),
    )


def _decode_text_file(payload: bytes) -> str:
    if not payload:
        raise RuntimeError("Файл пуст.")
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            value = payload.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            continue
        if "-->" in value:
            return value
    raise RuntimeError("Не удалось прочитать SRT. Сохраните его как UTF-8.")


async def _store_ready_srt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> None:
    state = context.user_data.get(_WIZARD_KEY) or {}
    project_id = str(state.get("project_id") or "")
    if state.get("awaiting") != "srt" or not project_id:
        raise RuntimeError("Сначала выберите режим «Мой готовый перевод — SRT» и пришлите ссылку.")

    store = DubStore()
    project = store.get_project(project_id)
    if not _project_owned_by(project, update.effective_user.id):
        raise PermissionError("Это не ваш проект.")
    metadata = project.get("metadata") or {}
    if str(metadata.get("translation_mode")) != _DIRECT_MODE:
        raise RuntimeError("Проект не находится в режиме готового SRT.")

    cues = parse_srt_text(text)
    root = _project_root(project_id)
    input_dir = root / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    ready_path = input_dir / "ready_translation.srt"
    ready_path.write_text(
        str(text).replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").rstrip() + "\n",
        encoding="utf-8",
    )

    job = store.enqueue_job(project_id, "render_direct")
    context.user_data.pop(_WIZARD_KEY, None)
    await update.effective_message.reply_text(
        "✅ <b>Готовый SRT принят</b>\n\n"
        f"Реплик: <b>{len(cues)}</b>\n"
        f"Рендер: задание <b>#{job['id']}</b>\n"
        f"Проект: <code>{html.escape(project_id)}</code>\n\n"
        "Русский текст не отправляется в Gemini и не будет изменён. "
        "Бот выполняет только техническую обработку таймкодов, озвучивание и мастеринг.\n\n"
        f"Статус: <code>/dubstatus {html.escape(project_id)}</code>",
        parse_mode="HTML",
    )


async def dubsrt_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    token = str((context.args or [""])[0]).strip().lower()
    try:
        project = (
            DubStore().get_project(token)
            if token
            else _latest_direct_draft(update.effective_user.id)
        )
        if not project:
            raise RuntimeError(
                "Не найден ожидающий SRT-проект. Откройте /dub и выберите режим готового SRT."
            )
        await _activate_srt_upload(update, context, str(project["id"]))
    except Exception as exc:
        await update.effective_message.reply_text(
            "⚠️ " + html.escape(_short(str(exc), 900)),
            parse_mode="HTML",
        )


async def dubtranslation_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Backward-compatible alias for older buttons/commands."""
    await dubsrt_command(update, context)


async def handle_dub_wizard_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    state = context.user_data.get(_WIZARD_KEY) or {}
    if not state:
        return
    if not await _admin(update):
        raise ApplicationHandlerStop

    awaiting = str(state.get("awaiting") or "")
    text = str(update.effective_message.text or "").strip()
    if awaiting == "url":
        try:
            await _create_generic_project(
                update,
                context,
                text,
                str(state.get("mode") or ""),
            )
        except Exception as exc:
            await update.effective_message.reply_text(
                "⚠️ "
                + html.escape(_short(str(exc), 900))
                + "\n\nПришлите корректную ссылку YouTube.",
                parse_mode="HTML",
            )
        raise ApplicationHandlerStop

    if awaiting == "srt":
        try:
            await _store_ready_srt(update, context, text)
        except Exception as exc:
            await update.effective_message.reply_text(
                "⚠️ SRT не принят: " + html.escape(_short(str(exc), 1600)),
                parse_mode="HTML",
            )
        raise ApplicationHandlerStop


async def handle_dub_wizard_document(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    state = context.user_data.get(_WIZARD_KEY) or {}
    document = update.effective_message.document
    if not document:
        return

    suffix = Path(document.file_name or "").suffix.casefold()
    if state.get("awaiting") != "srt":
        if suffix != ".srt" or not await _admin(update):
            return
        project = _latest_direct_draft(update.effective_user.id)
        if not project:
            return
        context.user_data[_WIZARD_KEY] = {
            "awaiting": "srt",
            "mode": _DIRECT_MODE,
            "project_id": str(project["id"]),
        }

    if not await _admin(update):
        raise ApplicationHandlerStop
    if suffix != ".srt":
        await update.effective_message.reply_text(
            "Нужен именно готовый файл <code>.srt</code>.",
            parse_mode="HTML",
        )
        raise ApplicationHandlerStop

    try:
        telegram_file = await context.bot.get_file(document.file_id)
        payload = await telegram_file.download_as_bytearray()
        if len(payload) > 2_000_000:
            raise RuntimeError("SRT слишком большой: максимум 2 МБ.")
        text = _decode_text_file(bytes(payload))
        await _store_ready_srt(update, context, text)
    except Exception as exc:
        await update.effective_message.reply_text(
            "⚠️ SRT не принят: " + html.escape(_short(str(exc), 1600)),
            parse_mode="HTML",
        )
    raise ApplicationHandlerStop


def register_dub_wizard_handlers(application: Any) -> None:
    if application.bot_data.get("dub_studio_wizard_registered"):
        return

    application.add_handler(
        CommandHandler("dub", dub_home_command, filters=_MSG_ONLY),
        group=0,
    )
    application.add_handler(
        CommandHandler("dubnewvideo", dubnewvideo_command, filters=_MSG_ONLY),
        group=-60,
    )
    application.add_handler(
        CommandHandler("dubsrt", dubsrt_command, filters=_MSG_ONLY),
        group=-60,
    )
    application.add_handler(
        CommandHandler("dubtranslation", dubtranslation_command, filters=_MSG_ONLY),
        group=-60,
    )
    application.add_handler(
        CallbackQueryHandler(handle_dub_wizard_callback, pattern=r"^dubwiz\|"),
        group=-60,
    )
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
    "_decode_text_file",
    "_extract_youtube_video_id",
    "_request_payload",
    "dub_home_command",
    "dubsrt_command",
    "dubtranslation_command",
    "handle_dub_wizard_callback",
    "handle_dub_wizard_document",
    "handle_dub_wizard_text",
    "register_dub_wizard_handlers",
]
