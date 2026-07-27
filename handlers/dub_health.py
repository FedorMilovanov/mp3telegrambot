#!/usr/bin/env python3
"""Local environment preflight for both Dub Studio production modes."""
from __future__ import annotations

import asyncio
import html
import importlib.util
import os
import shutil
import sys
import time
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
_WORKER_RUNTIME = "dub-worker-quality-v4.1"


def _check(label: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"label": label, "ok": bool(ok), "detail": str(detail)}


def _worker_is_current(worker: dict[str, Any] | None) -> bool:
    details = (worker or {}).get("details") or {}
    return worker_is_fresh(worker) and details.get("runtime") == _WORKER_RUNTIME


def _worker_snapshot_with_repair() -> dict[str, Any] | None:
    """Replace an idle legacy worker and return the newly registered snapshot."""
    store = DubStore()
    worker = store.latest_worker()
    if _worker_is_current(worker):
        return worker
    if str((worker or {}).get("status") or "") == "busy":
        return worker

    try:
        from services.dub_studio_runtime import ensure_worker_running

        requested = ensure_worker_running()
    except Exception:
        requested = False
    if not requested:
        return worker

    # The detached Windows worker normally registers immediately. Poll only
    # during this explicit admin check so the returned report reflects repair.
    for _ in range(30):
        worker = store.latest_worker()
        if _worker_is_current(worker):
            return worker
        time.sleep(0.1)
    return worker


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

    repo = Path(__file__).resolve().parents[1]
    quality_policy_path = repo / "tools" / "voxcpm2" / "dub_quality_v4.py"
    quality_guard_path = repo / "tools" / "voxcpm2" / "semantic_tts_guard_v4.py"
    quality_renderer_path = repo / "tools" / "voxcpm2" / "voxcpm2_quality_v4_renderer.py"
    quality_master_path = repo / "tools" / "voxcpm2" / "master_quality_v4.py"
    gemini_entry_path = repo / "tools" / "voxcpm2" / "generic_gemini_runtime.py"
    direct_entry_path = repo / "tools" / "voxcpm2" / "generic_direct_checked_runtime.py"
    contract_files = (
        quality_policy_path, quality_guard_path, quality_renderer_path,
        quality_master_path, gemini_entry_path, direct_entry_path,
    )
    contract_text = {
        path: path.read_text(encoding="utf-8") if path.is_file() else ""
        for path in contract_files
    }
    quality_ok = bool(
        all(contract_text.values())
        and "production._build_render_segments = build_render_segments_v4"
        in contract_text[quality_policy_path]
        and "local-window-v4.1" in contract_text[quality_policy_path]
        and '_GUARD_VERSION = "semantic-tts-guard-v4.1"'
        in contract_text[quality_guard_path]
        and "verify_timeline_v4" in contract_text[quality_guard_path]
        and "pipeline_signature" in contract_text[quality_guard_path]
        and 'env.pop("VOXCPM_PROMPT_TEXTS_JSON", None)'
        in contract_text[quality_guard_path]
        and '_QUALITY_VERSION = "voxcpm2-quality-v4.1"'
        in contract_text[quality_renderer_path]
        and "nested retry remains disabled" in contract_text[quality_renderer_path]
        and '"whole_mix_loudnorm": False' in contract_text[quality_master_path]
        and "alimiter=limit=0.985:level=false" in contract_text[quality_master_path]
        and "semantic_tts_guard_v4.install()" in contract_text[gemini_entry_path]
        and "semantic_tts_guard_v4.install()" in contract_text[direct_entry_path]
        and "legacy_semantic_guard.install = _disable_legacy_guard_install"
        in contract_text[gemini_entry_path]
        and "legacy_semantic_guard.install = _disable_legacy_guard_install"
        in contract_text[direct_entry_path]
    )
    checks.append(
        _check(
            "NoChew Quality v4.1",
            quality_ok,
            (
                "local windows + caption coverage; reference-only renderer; "
                "semantic/timing/chirp QA; signed checkpoints; exact-gain master; "
                "Gemini MAX + ready SRT entrypoints"
            ),
        )
    )

    whisper_available = importlib.util.find_spec("faster_whisper") is not None
    qa_model = os.getenv("DUB_TTS_QA_MODEL", "small").strip() or "small"
    checks.append(
        _check(
            "Whisper semantic QA",
            whisper_available,
            f"faster-whisper={'есть' if whisper_available else 'не найден'}; model={qa_model}",
        )
    )
    soundfile_available = importlib.util.find_spec("soundfile") is not None
    checks.append(
        _check(
            "SoundFile WAV I/O",
            soundfile_available,
            "soundfile есть" if soundfile_available else "soundfile не найден; установите requirements.txt",
        )
    )

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
        worker = _worker_snapshot_with_repair()
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
    checks = await asyncio.to_thread(collect_dub_health)
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
            "Оба режима требуют зелёные NoChew Quality v4.1, SoundFile, Whisper QA, FFmpeg, CPU Python, archive, storage и worker.",
            "Только Gemini MAX дополнительно требует рабочие Gemini keys; готовый SRT не отправляется на перевод или редактуру.",
        ]
    )
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


def register_dub_health_handler(application: Any) -> None:
    if application.bot_data.get("dub_studio_health_registered"):
        return
    application.add_handler(CommandHandler("dubcheck", dubcheck_command, filters=_MSG_ONLY))
    application.bot_data["dub_studio_health_registered"] = True


__all__ = ["collect_dub_health", "dubcheck_command", "register_dub_health_handler"]
