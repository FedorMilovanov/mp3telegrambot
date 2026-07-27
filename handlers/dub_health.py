#!/usr/bin/env python3
"""Local environment preflight for both Dub Studio production modes."""
from __future__ import annotations

import html
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, filters

from core.database import ADMIN_IDS
from services.dub_studio import (
    DubStore,
    load_recipe,
    studio_root,
    worker_is_fresh,
)
from tools.voxcpm2.dub_worker import build_command

_MSG_ONLY = filters.UpdateType.MESSAGE
_WORKER_RUNTIME = "dub-worker-tree-cancel-v2"


def _check(label: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"label": label, "ok": bool(ok), "detail": str(detail)}


def collect_dub_health() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    try:
        recipe = load_recipe("generic_short_v1")
        gemini, _ = build_command(recipe.recipe_id, "render_gemini")
        direct, _ = build_command(recipe.recipe_id, "render_direct")
        gemini_text = " ".join(gemini)
        direct_text = " ".join(direct)
        checks.append(
            _check(
                "Recipe: Gemini MAX",
                "tools.voxcpm2.generic_gemini_runtime" in gemini_text
                and "-Mode gemini" in gemini_text,
                gemini_text,
            )
        )
        checks.append(
            _check(
                "Recipe: готовый SRT",
                "tools.voxcpm2.generic_direct_checked_runtime" in direct_text
                and "-Mode direct" in direct_text,
                direct_text,
            )
        )
    except Exception as exc:
        checks.append(_check("Recipe-actions", False, str(exc)))

    for binary in ("ffmpeg", "ffprobe"):
        found = shutil.which(binary)
        checks.append(_check(binary, bool(found), found or "не найден в PATH"))

    cpu_venv = Path(
        os.getenv("DUB_CPU_VENV", r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv")
    )
    cpu_python = cpu_venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    checks.append(
        _check(
            "VoxCPM2 CPU Python",
            cpu_python.is_file(),
            str(cpu_python),
        )
    )

    archive = Path(
        os.getenv("DUB_VOX_ARCHIVE", r"C:\AI-Archive\VoxCPM2-paused-RTX3060")
    )
    checks.append(_check("VoxCPM2 archive", archive.is_dir(), str(archive)))

    gemini_names = (
        "GEMINI_API_KEY",
        "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3",
        "GEMINI_API_KEY_4",
    )
    available_keys = [name for name in gemini_names if os.getenv(name, "").strip()]
    checks.append(
        _check(
            "Gemini MAX keys",
            bool(available_keys),
            ", ".join(available_keys) if available_keys else "ключи не найдены",
        )
    )

    root = studio_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".dub-health-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks.append(_check("Dub Studio storage", True, str(root)))
    except Exception as exc:
        checks.append(_check("Dub Studio storage", False, f"{root}: {exc}"))

    try:
        worker = DubStore().latest_worker()
        details = (worker or {}).get("details") or {}
        fresh = worker_is_fresh(worker)
        version_ok = details.get("runtime") == _WORKER_RUNTIME
        checks.append(
            _check(
                "Worker",
                fresh and version_ok,
                (
                    f"status={(worker or {}).get('status')}; pid={(worker or {}).get('pid')}; "
                    f"runtime={details.get('runtime') or 'legacy'}"
                ),
            )
        )
    except Exception as exc:
        checks.append(_check("Worker", False, str(exc)))

    checks.append(
        _check(
            "Python UTF-8",
            (sys.stdout.encoding or "").lower().replace("-", "") == "utf8"
            or os.getenv("PYTHONUTF8") == "1",
            f"stdout={sys.stdout.encoding}; PYTHONUTF8={os.getenv('PYTHONUTF8', '')}",
        )
    )
    return checks


async def _admin(update: Update) -> bool:
    user = update.effective_user
    if user and user.id in ADMIN_IDS:
        return True
    if update.effective_message:
        await update.effective_message.reply_text("⛔ /dubcheck доступна только администратору.")
    return False


async def dubcheck_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _admin(update):
        return
    checks = collect_dub_health()
    passed = sum(1 for item in checks if item["ok"])
    lines = [
        "🩺 <b>Dub Studio — локальная проверка</b>",
        "",
        f"Пройдено: <b>{passed}/{len(checks)}</b>",
        "",
    ]
    for item in checks:
        icon = "✅" if item["ok"] else "❌"
        lines.append(
            f"{icon} <b>{html.escape(item['label'])}</b>\n"
            f"<code>{html.escape(item['detail'][:900])}</code>"
        )
    lines.extend(
        [
            "",
            "Gemini MAX требует зелёные Recipe, FFmpeg, CPU Python, archive, keys, storage и worker.",
            "Готовый SRT не требует Gemini keys, но остальные локальные проверки обязательны.",
        ]
    )
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


def register_dub_health_handler(application: Any) -> None:
    if application.bot_data.get("dub_studio_health_registered"):
        return
    application.add_handler(CommandHandler("dubcheck", dubcheck_command, filters=_MSG_ONLY))
    application.bot_data["dub_studio_health_registered"] = True


__all__ = ["collect_dub_health", "dubcheck_command", "register_dub_health_handler"]
