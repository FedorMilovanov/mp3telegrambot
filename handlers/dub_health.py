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
from services.dub_studio import DubStore, load_recipe, studio_root, worker_is_fresh
from tools.voxcpm2.dub_worker import build_command

_MSG_ONLY = filters.UpdateType.MESSAGE
_WORKER_RUNTIME = "dub-worker-quality-v4.4"


def _check(label: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"label": label, "ok": bool(ok), "detail": str(detail)}


def _worker_is_current(worker: dict[str, Any] | None) -> bool:
    details = (worker or {}).get("details") or {}
    return worker_is_fresh(worker) and details.get("runtime") == _WORKER_RUNTIME


def _worker_snapshot_with_repair() -> dict[str, Any] | None:
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
    for _ in range(30):
        worker = store.latest_worker()
        if _worker_is_current(worker):
            return worker
        time.sleep(0.1)
    return worker


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _quality_contract(repo: Path) -> tuple[bool, str]:
    voxcpm = repo / "tools" / "voxcpm2"
    example = voxcpm / "examples" / "john_piper_z20py4yqhyq"
    paths = {
        "core": voxcpm / "clean_production_core.py",
        "normalizer": voxcpm / "clean_segment_normalizer.py",
        "expression": voxcpm / "expressive_continuity.py",
        "translation": voxcpm / "expressive_translation.py",
        "reference": voxcpm / "professional_audio_v45.py",
        "qa": voxcpm / "professional_audio_qa_v45.py",
        "direct_io": voxcpm / "direct_max_quality_io.py",
        "direct_analysis": voxcpm / "direct_max_quality_analysis.py",
        "direct_timbre": voxcpm / "direct_timbre_analysis.py",
        "direct_render": voxcpm / "direct_max_quality_render.py",
        "direct_cli": voxcpm / "direct_max_quality_cli.py",
        "final_media_qa": voxcpm / "final_media_qa.py",
        "stable_cli": example / "voxcpm2_cpu_shorts_production.py",
        "master": example / "master_constant_mix.py",
        "gemini": voxcpm / "generic_clean_gemini_runtime.py",
        "direct": voxcpm / "generic_clean_direct_runtime.py",
        "custom": voxcpm / "generic_clean_custom_runtime.py",
        "repair": voxcpm / "generic_clean_audio_repair_runtime.py",
        "runtime": repo / "services" / "dub_studio_runtime.py",
        "worker": voxcpm / "dub_worker_hardened.py",
        "title": repo / "services" / "dub_title_policy.py",
    }
    text = {name: _read(path) for name, path in paths.items()}
    missing = [name for name, value in text.items() if not value]
    if missing:
        return False, "не найдены: " + ", ".join(missing)

    renderer_text = "\n".join(
        text[name]
        for name in (
            "direct_io",
            "direct_analysis",
            "direct_timbre",
            "direct_render",
            "direct_cli",
            "stable_cli",
        )
    )
    contracts = {
        "direct-policy": 'POLICY = "voxcpm2-direct-max-quality-v2"' in text["direct_io"],
        "native-16to48": (
            "EXPECTED_ENCODE_SR = 16000" in text["direct_io"]
            and "EXPECTED_OUTPUT_SR = 48000" in text["direct_io"]
            and "AudioVAE:" in text["direct_cli"]
        ),
        "fingerprints": (
            '"reference_sha256"' in text["direct_cli"]
            and '"model_config_sha256"' in text["direct_cli"]
        ),
        "retry-badcase": (
            '"retry_badcase": True' in text["direct_render"]
            and '"retry_badcase_max_times": 2' in text["direct_render"]
        ),
        "candidate-voice": (
            "candidate_hard_ok" in text["direct_cli"]
            and "voiced_ratio" in text["direct_analysis"]
            and "spectral_similarity" in text["direct_analysis"]
            and "HARD_SIMILARITY_FLOOR = 0.30" in text["direct_timbre"]
        ),
        "natural-timing": (
            "MAX_TEMPO = 1.35" in text["direct_io"]
            and "MAX_SECONDS = 5.4" in text["core"]
            and "Russian tokens preserved" in text["normalizer"]
        ),
        "clean-reference": (
            "REFERENCE_TAIL_SILENCE = 0.0" in text["direct_io"]
            and '"denoise": False' in text["reference"]
            and "afftdn" not in text["reference"]
        ),
        "semantic-and-acoustic-QA-v3": (
            'POLICY = "clean-expression-aware-qa-v3"' in text["qa"]
            and "_forced_russian_fallback" in text["qa"]
            and "confident_foreign_block" in text["qa"]
            and "continuity_v45" in text["qa"]
            and "def _voice_limits(" in text["qa"]
        ),
        "same-cli": (
            "from tools.voxcpm2.direct_max_quality_cli import main" in text["stable_cli"]
            and "voxcpm2_cpu_shorts_production.py" in text["core"]
            and "subprocess.run(command" in text["core"]
        ),
        "no-wrapper": (
            "runpy.run_path" not in renderer_text
            and "class _SubprocessProxy" not in renderer_text
            and "semantic_tts_guard" not in renderer_text
            and '"wrapper_count": 0' in text["core"]
        ),
        "expression": (
            'POLICY = "source-guided-expression-v1"' in text["expression"]
            and "def _smooth(" in text["expression"]
            and "build_controlled_expressive_reference" in text["expression"]
        ),
        "translation": (
            'POLICY = "expressive-spoken-translation-v1"' in text["translation"]
            and "намеренные повторы" in text["translation"]
            and "риторические вопросы" in text["translation"]
            and "production.translate_groups_max = expressive_translation.translate_groups"
            in text["gemini"]
        ),
        "clean-entrypoints": (
            "TTS guard disabled" in text["gemini"]
            and "TTS guard disabled" in text["direct"]
            and "TTS guard disabled" in text["custom"]
            and "force_fresh=repair_all" in text["repair"]
            and 'gemini_called": False' in text["repair"]
        ),
        "master-and-final-AAC-QA": (
            "linear=true" in text["master"]
            and "10.0 ** (float(target_tp) / 20.0)" in text["master"]
            and "verify_final_outputs" in text["master"]
            and "final_media_verification.json" in text["master"]
            and "Отчёт сохранён" in text["final_media_qa"]
            and "container_duration_delta_seconds" in text["final_media_qa"]
            and "audio_duration_delta_seconds" in text["final_media_qa"]
            and "TRUE_PEAK_DELIVERY_CEILING_DBTP = -1.0" in text["final_media_qa"]
            and "EXPECTED_SAMPLE_RATE = 48_000" in text["final_media_qa"]
            and "MASTER_I = -16.0" in text["core"]
            and "MASTER_TP = -1.5" in text["core"]
        ),
        "editable-progress": (
            "edit_message_text" in text["runtime"]
            and "dub_progress_message_v1" in text["runtime"]
            and "_finalize_progress_card" in text["runtime"]
            and "dub_progress_updates" not in text["runtime"]
        ),
        "worker-v44": (
            'dub-worker-quality-v4.4' in text["runtime"]
            and 'dub-worker-quality-v4.4' in text["worker"]
            and "_progress_from_line_v44" in text["worker"]
            and 'return current, ""' in text["worker"]
        ),
        "single-title-policy": "install_dub_title_policy" in text["title"],
    }
    failed = [name for name, ok in contracts.items() if not ok]
    return (
        not failed,
        "все контракты активны" if not failed else "не прошли: " + ", ".join(failed),
    )


def collect_dub_health() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    try:
        recipe = load_recipe("generic_short_v1")
        gemini, _ = build_command(recipe.recipe_id, "render_gemini")
        direct, _ = build_command(recipe.recipe_id, "render_direct")
        repair, repair_spec = build_command(recipe.recipe_id, "repair_audio")
        checks.extend(
            [
                _check(
                    "Recipe: Gemini MAX",
                    "tools.voxcpm2.generic_clean_gemini_runtime" in " ".join(gemini)
                    and "-Mode gemini" in " ".join(gemini),
                    " ".join(gemini),
                ),
                _check(
                    "Recipe: готовый SRT",
                    "tools.voxcpm2.generic_clean_direct_runtime" in " ".join(direct)
                    and "-Mode direct" in " ".join(direct),
                    " ".join(direct),
                ),
                _check(
                    "Recipe: чистый аудиоремонт",
                    "tools.voxcpm2.generic_clean_audio_repair_runtime" in " ".join(repair)
                    and str(repair_spec.get("kind") or "") == "utility",
                    " ".join(repair),
                ),
            ]
        )
    except Exception as exc:
        checks.extend(
            _check(label, False, str(exc))
            for label in (
                "Recipe: Gemini MAX",
                "Recipe: готовый SRT",
                "Recipe: чистый аудиоремонт",
            )
        )

    repo = Path(__file__).resolve().parents[1]
    quality_ok, quality_detail = _quality_contract(repo)
    checks.append(
        _check(
            "Clean Expressive NoChew + независимый QA",
            quality_ok,
            quality_detail
            + "; direct 16→48k; fingerprinted refs/model; retry_badcase; "
            "F0/voiced+timbre gate; QA v3 auto+forced-RU with foreign block; "
            "final AAC BS.1770 QA; editable progress; worker v4.4; -16 LUFS/-1.5 dBTP",
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
            "soundfile есть" if soundfile_available else "soundfile не найден",
        )
    )
    for binary in ("ffmpeg", "ffprobe"):
        found = shutil.which(binary)
        checks.append(_check(binary, bool(found), found or "не найден в PATH"))

    cpu_venv = Path(
        os.getenv("DUB_CPU_VENV", r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv")
    )
    cpu_python = cpu_venv / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    checks.append(_check("VoxCPM2 CPU Python", cpu_python.is_file(), str(cpu_python)))

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
        checks.append(
            _check(
                "Worker",
                worker_is_fresh(worker) and details.get("runtime") == _WORKER_RUNTIME,
                f"status={(worker or {}).get('status')}; pid={(worker or {}).get('pid')}; "
                f"runtime={details.get('runtime') or 'legacy'}",
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
        await update.effective_message.reply_text(
            "⛔ /dubcheck доступна только администратору."
        )
    return False


async def dubcheck_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
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
    application.add_handler(
        CommandHandler("dubcheck", dubcheck_command, filters=_MSG_ONLY)
    )
    application.bot_data["dub_studio_health_registered"] = True


__all__ = ["collect_dub_health", "dubcheck_command", "register_dub_health_handler"]
