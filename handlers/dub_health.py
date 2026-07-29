#!/usr/bin/env python3
"""Local environment preflight for all Dub Studio production actions."""
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
_WORKER_RUNTIME = "dub-worker-quality-v4.3"
_CLEAN_POLICY = "clean-direct-production-v1"
_EXPRESSION_POLICY = "source-guided-expression-v1"
_TRANSLATION_POLICY = "expressive-spoken-translation-v1"


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


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def collect_dub_health() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    try:
        recipe = load_recipe("generic_short_v1")
        gemini, _ = build_command(recipe.recipe_id, "render_gemini")
        direct, _ = build_command(recipe.recipe_id, "render_direct")
        repair, repair_spec = build_command(recipe.recipe_id, "repair_audio")
        gemini_text = " ".join(gemini)
        direct_text = " ".join(direct)
        repair_text = " ".join(repair)
        checks.append(
            _check(
                "Recipe: Gemini MAX",
                "tools.voxcpm2.generic_clean_gemini_runtime" in gemini_text
                and "-Mode gemini" in gemini_text,
                gemini_text,
            )
        )
        checks.append(
            _check(
                "Recipe: готовый SRT",
                "tools.voxcpm2.generic_clean_direct_runtime" in direct_text
                and "-Mode direct" in direct_text,
                direct_text,
            )
        )
        checks.append(
            _check(
                "Recipe: чистый аудиоремонт",
                "tools.voxcpm2.generic_clean_audio_repair_runtime" in repair_text
                and str(repair_spec.get("kind") or "") == "utility",
                repair_text,
            )
        )
    except Exception as exc:
        checks.append(_check("Recipe-actions", False, str(exc)))

    repo = Path(__file__).resolve().parents[1]
    clean_core_path = repo / "tools" / "voxcpm2" / "clean_production_core.py"
    clean_normalizer_path = repo / "tools" / "voxcpm2" / "clean_segment_normalizer.py"
    expression_path = repo / "tools" / "voxcpm2" / "expressive_continuity.py"
    translation_path = repo / "tools" / "voxcpm2" / "expressive_translation.py"
    clean_gemini_path = repo / "tools" / "voxcpm2" / "generic_clean_gemini_runtime.py"
    clean_direct_path = repo / "tools" / "voxcpm2" / "generic_clean_direct_runtime.py"
    clean_custom_path = repo / "tools" / "voxcpm2" / "generic_clean_custom_runtime.py"
    clean_repair_path = repo / "tools" / "voxcpm2" / "generic_clean_audio_repair_runtime.py"
    reference_policy_path = repo / "tools" / "voxcpm2" / "professional_audio_v45.py"
    qa_path = repo / "tools" / "voxcpm2" / "professional_audio_qa_v45.py"
    renderer_path = (
        repo
        / "tools"
        / "voxcpm2"
        / "examples"
        / "john_piper_z20py4yqhyq"
        / "voxcpm2_cpu_shorts_production.py"
    )
    master_path = (
        repo
        / "tools"
        / "voxcpm2"
        / "examples"
        / "john_piper_z20py4yqhyq"
        / "master_constant_mix.py"
    )
    contract_files = (
        clean_core_path,
        clean_normalizer_path,
        expression_path,
        translation_path,
        clean_gemini_path,
        clean_direct_path,
        clean_custom_path,
        clean_repair_path,
        reference_policy_path,
        qa_path,
        renderer_path,
        master_path,
    )
    contract_text = {path: _read(path) for path in contract_files}
    core = contract_text[clean_core_path]
    normalizer = contract_text[clean_normalizer_path]
    expression = contract_text[expression_path]
    translation = contract_text[translation_path]
    gemini_entry = contract_text[clean_gemini_path]
    direct_entry = contract_text[clean_direct_path]
    custom_entry = contract_text[clean_custom_path]
    repair_entry = contract_text[clean_repair_path]
    reference_policy = contract_text[reference_policy_path]
    qa_contract = contract_text[qa_path]

    quality_ok = bool(
        all(contract_text.values())
        and f'POLICY = "{_CLEAN_POLICY}"' in core
        and "voxcpm2_cpu_shorts_production.py" in core
        and "master_constant_mix.py" in core
        and "subprocess.run(command" in core
        and "professional_audio_qa_v45.verify_timeline_v45" in core
        and '"wrapper_count": 0' in core
        and "semantic_tts_guard_v4.install(" not in core
        and "professional_audio_v45.install(" not in core
        and "VOXCPM_ORIGINAL_RENDERER" in core
        and "env.pop(key, None)" in core
        and f'POLICY = "{_EXPRESSION_POLICY}"' in expression
        and "def _smooth(" in expression
        and "build_controlled_expressive_reference" in expression
        and 'return "composite" if tier in {"emphatic", "passionate"}' in expression
        and "shouting rejected" in expression
        and f'POLICY = "{_TRANSLATION_POLICY}"' in translation
        and "намеренные повторы" in translation
        and "риторические вопросы" in translation
        and "Не превращай фразу в конспект" in translation
        and "production.translate_groups_max = expressive_translation.translate_groups" in gemini_entry
        and "build_reference_v45" in reference_policy
        and "reference calm windows" in reference_policy
        and "voice_match_v45" in qa_contract
        and "continuity_v45" in qa_contract
        and "TTS guard disabled" in gemini_entry
        and "TTS guard disabled" in direct_entry
        and "TTS guard disabled" in custom_entry
        and "install_runtime_adapters = _install_clean_runtime_adapters" in gemini_entry
        and "install_runtime_adapters = _install_clean_runtime_adapters" in direct_entry
        and "install_runtime_adapters = _install_clean_runtime_adapters" in custom_entry
        and "force_fresh=repair_all" in repair_entry
        and 'translation_reused": True' in repair_entry
        and 'gemini_called": False' in repair_entry
        and "Russian tokens preserved" in normalizer
        and "semantic_tts_guard_v47" not in repair_entry
        and "semantic_tts_guard_v46" not in repair_entry
    )
    checks.append(
        _check(
            "Clean Expressive NoChew + независимый QA",
            quality_ok,
            (
                "короткие окна <=5.4с; source-guided плавная эмоциональная дуга; "
                "спокойный + controlled-expressive реальные референсы; rhetoric-preserving "
                "Gemini MAX; прямой renderer/master; wrapper_count=0; -16 LUFS/-1.5 dBTP"
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
            "Все действия требуют зелёные Clean Expressive NoChew, SoundFile, Whisper QA, FFmpeg, CPU Python, archive, storage и worker.",
            "Только Gemini MAX требует рабочие Gemini keys; готовый SRT и аудиоремонт не отправляются на перевод или редактуру.",
        ]
    )
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


def register_dub_health_handler(application: Any) -> None:
    if application.bot_data.get("dub_studio_health_registered"):
        return
    application.add_handler(CommandHandler("dubcheck", dubcheck_command, filters=_MSG_ONLY))
    application.bot_data["dub_studio_health_registered"] = True


__all__ = ["collect_dub_health", "dubcheck_command", "register_dub_health_handler"]
