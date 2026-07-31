#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reliable multiline Dub commands and stale callback-card recovery."""
from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from telegram.error import BadRequest
from telegram.ext import (
    ApplicationHandlerStop,
    ContextTypes,
    MessageHandler,
    filters,
)

MULTICOMMAND_POLICY = "dub-multiline-command-dispatch-v1"
STALE_CARD_POLICY = "dub-callback-edit-or-reply-v1"
_MAX_COMMANDS = 8
_MULTI_DUB_PATTERN = re.compile(r"(?is)^\s*/dub[^\n]*\n\s*/dub")
_COMMAND_LINE = re.compile(
    r"^/(?P<name>[a-z0-9_]+)(?:@[a-z0-9_]+)?(?:\s+(?P<args>.*))?$",
    re.IGNORECASE,
)
_SUPPORTED_COMMANDS = frozenset(
    {
        "dub",
        "dubcheck",
        "dubnew",
        "dubnewvideo",
        "dubsrt",
        "dubtranslation",
        "dublist",
        "dubstatus",
        "dublog",
        "dubrun",
        "dubrepair",
        "dubcancel",
        "dubfiles",
        "dubworker",
        "dubsend",
        "dubsegments",
        "dubfix",
    }
)
_PERMANENT_EDIT_ERRORS = (
    "message to edit not found",
    "message can't be edited",
    "message cannot be edited",
    "message identifier is not specified",
    "message_id_invalid",
)


def parse_dub_command_lines(text: str) -> list[tuple[str, list[str]]]:
    """Parse only complete, recognized Dub commands from one Telegram message."""
    result: list[tuple[str, list[str]]] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _COMMAND_LINE.fullmatch(line)
        if match is None:
            return []
        name = str(match.group("name") or "").casefold()
        if name not in _SUPPORTED_COMMANDS:
            return []
        arguments = str(match.group("args") or "").split()
        result.append((name, arguments))
        if len(result) > _MAX_COMMANDS:
            return []
    return result if len(result) >= 2 else []


def _command_handlers() -> dict[str, Callable[..., Awaitable[None]]]:
    from handlers import dub_audio_repair
    from handlers import dub_commands
    from handlers import dub_delivery
    from handlers import dub_health
    from handlers import dub_wizard

    return {
        "dub": dub_wizard.dub_home_command,
        "dubcheck": dub_health.dubcheck_command,
        "dubnew": dub_commands.dubnew_command,
        "dubnewvideo": dub_wizard.dubnewvideo_command,
        "dubsrt": dub_wizard.dubsrt_command,
        "dubtranslation": dub_wizard.dubtranslation_command,
        "dublist": dub_commands.dublist_command,
        "dubstatus": dub_commands.dubstatus_command,
        "dublog": dub_commands.dublog_command,
        "dubrun": dub_commands.dubrun_command,
        "dubrepair": dub_commands.dubrepair_command,
        "dubcancel": dub_commands.dubcancel_command,
        "dubfiles": dub_commands.dubfiles_command,
        "dubworker": dub_commands.dubworker_command,
        "dubsend": dub_delivery.dubsend_command,
        "dubsegments": dub_audio_repair.dubsegments_command,
        "dubfix": dub_audio_repair.dubfix_command,
    }


async def handle_dub_multicommand(
    update: Any,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Execute every command line once, then stop the normal first-command pass."""
    message = update.effective_message
    parsed = parse_dub_command_lines(getattr(message, "text", ""))
    if not parsed:
        return

    handlers = _command_handlers()
    original_args = list(context.args or [])
    try:
        for name, arguments in parsed:
            context.args = list(arguments)
            await handlers[name](update, context)
    finally:
        context.args = original_args
    raise ApplicationHandlerStop


def _permanent_edit_failure(exc: BaseException) -> bool:
    detail = str(exc or "").casefold()
    return any(marker in detail for marker in _PERMANENT_EDIT_ERRORS)


def install_stale_card_fallback() -> None:
    """Patch callback edits so an expired card is replaced instead of failing."""
    from handlers import dub_commands

    current = dub_commands._safe_edit
    if getattr(current, "_dub_stale_card_fallback", False):
        return

    async def safe_edit_or_reply(query: Any, text: str, **kwargs: Any) -> bool:
        try:
            return await current(query, text, **kwargs)
        except BadRequest as exc:
            if not _permanent_edit_failure(exc):
                raise
            message = getattr(query, "message", None)
            if message is None or not hasattr(message, "reply_text"):
                raise
            await message.reply_text(text, **kwargs)
            return True

    safe_edit_or_reply._dub_stale_card_fallback = True  # type: ignore[attr-defined]
    dub_commands._safe_edit = safe_edit_or_reply


def register_dub_multicommand_handler(application: Any) -> None:
    if application.bot_data.get("dub_multicommand_registered"):
        return
    install_stale_card_fallback()
    application.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE
            & filters.TEXT
            & filters.Regex(_MULTI_DUB_PATTERN),
            handle_dub_multicommand,
            block=True,
        ),
        group=-100,
    )
    application.bot_data["dub_multicommand_registered"] = True


__all__ = [
    "MULTICOMMAND_POLICY",
    "STALE_CARD_POLICY",
    "handle_dub_multicommand",
    "install_stale_card_fallback",
    "parse_dub_command_lines",
    "register_dub_multicommand_handler",
]
