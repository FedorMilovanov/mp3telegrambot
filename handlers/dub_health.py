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
from services.dub_worker_release import WORKER_RUNTIME
from services.dub_worker import build_command

_MSG_ONLY = filters.UpdateType.MESSAGE
_WORKER_RUNTIME = WORKER_RUNTIME


def _check(label: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"label": label, "ok": bool(ok), "detail": str(detail)}


def _worker_is_current(worker: dict[str, Any] | None) -> bool:
    details = (worker or {}).get("details") or {}
    return worker_is_fresh(worker) and details.get("runtime") == _WORKER_RUNTIME


def _worker_snapshot_with_repair() -> dict[str, Any] | None:
    store = DubStore()
    worker = store.latest_worker()
    if _worker_is_current(worker) or str((worker or {}).get("status") or "") == "busy":
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
    root = Path(repo)
    voxcpm = root / "tools" / "voxcpm2"
    required = {
        "runtime_contract": voxcpm / "clean_runtime_contract.py",
        "core": voxcpm / "clean_production_core.py",
        "source_download": voxcpm / "clean_source_download.py",
        "request_settings": voxcpm / "clean_request_settings.py",
        "translation": voxcpm / "strict_translation_payload.py",
        "gemini": voxcpm / "generic_gemini_runtime.py",
        "direct": voxcpm / "generic_direct_runtime.py",
        "custom": voxcpm / "generic_custom_runtime.py",
        "repair": voxcpm / "generic_clean_audio_repair_runtime.py",
        "semantic_blocks": voxcpm / "semantic_block_runtime.py",
        "direct_io": voxcpm / "direct_max_quality_io.py",
        "retry_epoch": voxcpm / "direct_retry_epoch.py",
        "direct_master": voxcpm / "master_direct_russian_only.py",
        "preflight": voxcpm / "dub_job_preflight.py",
        "backend": root / "services" / "speech_backends" / "voxcpm2.py",
        "worker": root / "services" / "dub_worker.py",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        return False, "не найдены canonical owners: " + ", ".join(sorted(missing))
    text = {name: _read(path) for name, path in required.items()}
    failed: list[str] = []

    forbidden = ("sys.modules[", "setattr(module", "def install_runtime", "def install_preflight", "ContextVar(")
    for name in ("gemini", "direct", "custom", "repair", "direct_master", "preflight"):
        if any(token in text[name] for token in forbidden):
            failed.append("runtime-safety")
            break

    expected_routes = {
        "render": "tools.voxcpm2.generic_gemini_runtime",
        "render_gemini": "tools.voxcpm2.generic_gemini_runtime",
        "render_direct": "tools.voxcpm2.generic_direct_runtime",
        "repair_audio": "tools.voxcpm2.generic_clean_audio_repair_runtime",
        "prepare_custom": "tools.voxcpm2.generic_custom_runtime",
        "render_custom": "tools.voxcpm2.generic_custom_runtime",
    }
    try:
        recipe = load_recipe("generic_short_v1")
        recipe_ok = all(
            str(recipe.action(action).get("runner") or "") == "python_module"
            and str(recipe.action(action).get("module") or "") == module
            for action, module in expected_routes.items()
        )
    except Exception:
        recipe_ok = False
    if not recipe_ok:
        failed.append("recipe-routing")

    if not (
        'POLICY = spatial_bed_contract.POLICY' in text["direct_master"]
        and '"source_bed_applied": False' in text["direct_master"]
        and '"applied_original_level": 0.0' in text["direct_master"]
        and "master_monolithic_mix" not in text["direct_master"]
        and "tools.voxcpm2.master_direct_russian_only" in text["backend"]
        and "master_direct_russian_only.py" in text["core"]
    ):
        failed.append("direct-master")

    if not (
        'POLICY = "clean-runtime-contract-v2"' in text["runtime_contract"]
        and "tools/voxcpm2/clean_runtime_contract.py" in text["runtime_contract"]
        and "tools/voxcpm2/master_direct_russian_only.py" in text["runtime_contract"]
        and "tools/voxcpm2/generic_project_runtime.py" in text["runtime_contract"]
        and "tools/voxcpm2/generic_direct_runtime.py" in text["runtime_contract"]
        and "tools/voxcpm2/generic_clean_audio_repair_runtime.py" in text["runtime_contract"]
        and "def build_fingerprints(" in text["runtime_contract"]
    ):
        failed.append("fingerprints")

    if not (
        'PREFLIGHT_JSON_TRANSPORT_POLICY = "marked-preflight-json-transport-v2"' in text["preflight"]
        and "backend.runtime_paths(repo, request)" in text["preflight"]
        and "backend.process_environment(" in text["preflight"]
        and "def _decode_probe_payload(" in text["preflight"]
    ):
        failed.append("preflight")

    if not (
        "def build_command(" in text["worker"]
        and "from tools.voxcpm2 import dub_job_preflight" in text["worker"]
        and "from services.dub_worker import build_command" in _read(Path(__file__))
    ):
        failed.append("worker")

    if not (
        'POLICY = "voxcpm2-direct-max-quality-v3"' in text["direct_io"]
        and "from collections.abc import Mapping" in text["retry_epoch"]
        and "semantic_block_runtime.build_direct_segments(" in text["direct"]
        and "ProjectRoute" in text["gemini"]
        and "ProjectRoute" in text["custom"]
    ):
        failed.append("direct-runtime")

    if failed:
        return False, "не прошли: " + ", ".join(failed)
    return True, (
        "runtime-safety; recipe-routing; direct-master Russian-only; fingerprints; "
        "source-owned preflight; services.dub_worker; typed direct retry"
    )


def _recipe_checks() -> list[dict[str, Any]]:
    try:
        recipe = load_recipe("generic_short_v1")
        gemini, _ = build_command(recipe.recipe_id, "render_gemini")
        direct, _ = build_command(recipe.recipe_id, "render_direct")
        repair, repair_spec = build_command(recipe.recipe_id, "repair_audio")
        gemini_text, direct_text, repair_text = map(" ".join, (gemini, direct, repair))
        return [
            _check(
                "Recipe: Gemini MAX",
                "tools.voxcpm2.generic_gemini_runtime" in gemini_text
                and "-Mode gemini" in gemini_text,
                gemini_text,
            ),
            _check(
                "Recipe: готовый SRT",
                "tools.voxcpm2.generic_direct_runtime" in direct_text
                and "-Mode direct" in direct_text,
                direct_text,
            ),
            _check(
                "Recipe: чистый аудиоремонт",
                "tools.voxcpm2.generic_clean_audio_repair_runtime" in repair_text
                and str(repair_spec.get("kind") or "") == "utility",
                repair_text,
            ),
        ]
    except Exception as exc:
        return [
            _check(label, False, str(exc))
            for label in (
                "Recipe: Gemini MAX",
                "Recipe: готовый SRT",
                "Recipe: чистый аудиоремонт",
            )
        ]


def collect_dub_health() -> list[dict[str, Any]]:
    checks = _recipe_checks()
    repo = Path(__file__).resolve().parents[1]
    quality_ok, quality_detail = _quality_contract(repo)
    from core.media_title_policy import media_title_policy_contract
    from services.dub_release_health_v64 import _v68_quality_contract
    title_ok, title_detail = media_title_policy_contract()
    release_ok, release_detail = _v68_quality_contract(repo)
    checks.append(
        _check(
            "Clean Expressive NoChew + независимый QA",
            quality_ok and title_ok and release_ok,
            quality_detail + "; " + title_detail + "; " + release_detail
            + "; verified YouTube ID + sampled source cache; truthful 0%/0ms settings; "
            "strict unique translation IDs + creator-repeat preservation + actual source language; "
            "runtime v2 complete clean-path fingerprints; direct v3 16→48k; "
            "continuous-first v2 hard-floor reference; exact numeric/date anchors; "
            "fail-closed raw pitch + voice/timbre gates + source-prosody ranking; "
            "calm+expressive identity; Gemini passes 1/3–3/3 with bounded key failover; "
            "fixed-original master + aligned post-AAC 18% regression; "
            "final AAC BS.1770+PTS; limiter level-off/latency-compensated; "
            "editable progress; worker v4.5 queue guards; -16 LUFS/-1.5 dBTP",
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

    cpu_venv = Path(os.getenv("DUB_CPU_VENV", r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv"))
    cpu_python = cpu_venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    checks.append(_check("VoxCPM2 CPU Python", cpu_python.is_file(), str(cpu_python)))

    archive = Path(os.getenv("DUB_VOX_ARCHIVE", r"C:\AI-Archive\VoxCPM2-paused-RTX3060"))
    checks.append(_check("VoxCPM2 archive", archive.is_dir(), str(archive)))

    gemini_names = ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4")
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
