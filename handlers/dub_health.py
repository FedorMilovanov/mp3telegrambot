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
    voxcpm = repo / "tools" / "voxcpm2"
    example = voxcpm / "examples" / "john_piper_z20py4yqhyq"
    paths = {
        "core": voxcpm / "clean_production_core.py",
        "runtime_contract": voxcpm / "clean_runtime_contract.py",
        "source_download": voxcpm / "clean_source_download.py",
        "request_settings": voxcpm / "clean_request_settings.py",
        "strict_translation": voxcpm / "strict_translation_payload.py",
        "creator_vtt": voxcpm / "generic_gemini_runtime.py",
        "normalizer": voxcpm / "clean_segment_normalizer.py",
        "expression": voxcpm / "expressive_continuity.py",
        "continuous_reference": voxcpm / "continuous_reference_policy.py",
        "reference_gate": voxcpm / "controlled_reference_gate.py",
        "numeric": voxcpm / "russian_spoken_numbers.py",
        "translation": voxcpm / "expressive_translation.py",
        "gemini_runtime": voxcpm / "generic_short_runtime.py",
        "reference": voxcpm / "professional_audio_v45.py",
        "qa": voxcpm / "professional_audio_qa_v45.py",
        "io": voxcpm / "direct_max_quality_io.py",
        "analysis": voxcpm / "direct_max_quality_analysis.py",
        "prosody": voxcpm / "direct_source_prosody.py",
        "timbre": voxcpm / "direct_timbre_analysis.py",
        "render": voxcpm / "direct_max_quality_render.py",
        "cli": voxcpm / "direct_max_quality_cli.py",
        "media_qa": voxcpm / "final_media_qa.py",
        "stable_cli": example / "voxcpm2_cpu_shorts_production.py",
        "master": example / "master_constant_mix.py",
        "gemini": voxcpm / "generic_clean_gemini_runtime.py",
        "direct": voxcpm / "generic_direct_runtime.py",
        "custom": voxcpm / "generic_clean_custom_runtime.py",
        "repair": voxcpm / "generic_clean_audio_repair_runtime.py",
        "runtime": repo / "services" / "dub_studio_runtime.py",
        "worker": voxcpm / "dub_worker_hardened.py",
        "title": repo / "core" / "media_title_policy.py",
    }
    text = {name: _read(path) for name, path in paths.items()}
    missing = [name for name, value in text.items() if not value]
    if missing:
        return False, "не найдены: " + ", ".join(missing)

    renderer_text = "\n".join(
        text[name]
        for name in (
            "io",
            "analysis",
            "prosody",
            "timbre",
            "render",
            "cli",
            "stable_cli",
        )
    )
    route_names = ("gemini", "direct", "custom", "repair")
    source_route_names = ("gemini", "direct", "custom")
    contracts = {
        "runtime-contract-v2": (
            'POLICY = "clean-runtime-contract-v2"' in text["runtime_contract"]
            and "def sampled_sha256_file(" in text["runtime_contract"]
            and '"sampled-begin-middle-end-v1"' in text["runtime_contract"]
            and 'root.rglob("*.py")' in text["runtime_contract"]
            and "def _setting(" in text["runtime_contract"]
            and '_setting(request, "base_seed", 2026072800)' in text["runtime_contract"]
            and 'request.get("base_seed") or' not in text["runtime_contract"]
            and '"tools/voxcpm2/direct_source_prosody.py"' in text["runtime_contract"]
            and '"tools/voxcpm2/clean_source_download.py"' in text["runtime_contract"]
            and '"tools/voxcpm2/clean_request_settings.py"' in text["runtime_contract"]
            and '"tools/voxcpm2/strict_translation_payload.py"' in text["runtime_contract"]
            and '"release_complete": False' in text["core"]
            and "release_complete=True" in text["core"]
            and "direct_cli_runtime.marker.json" in text["stable_cli"]
        ),
        "verified-source-cache": (
            'POLICY = "clean-source-download-manifest-v1"' in text["source_download"]
            and "def _url_video_id(" in text["source_download"]
            and "def _sampled_sha256(" in text["source_download"]
            and 'source.with_suffix(source.suffix + ".download.json")' in text["source_download"]
            and "YouTube URL и yt-dlp metadata указывают на разные ролики" in text["source_download"]
            and all(
                "hardened.download_source = clean_source_download.download_source" in text[name]
                and "hardened.pipeline.download_source = clean_source_download.download_source" in text[name]
                for name in ("gemini", "custom")
            )
            and "clean_source_download.download_source(source_url, source)" in text["direct"]
        ),
        "truthful-request-settings": (
            'POLICY = "clean-request-settings-v1"' in text["request_settings"]
            and "def _setting(" in text["request_settings"]
            and "original_level не может быть bool" in text["request_settings"]
            and "russian_delay_ms не может быть bool" in text["request_settings"]
            and "def repair_manifest(" in text["request_settings"]
            and 'payload["settings_policy"] = POLICY' in text["request_settings"]
            and all(
                "clean_request_settings.russian_delay_ms(request)" in text[name]
                and "clean_request_settings.repair_manifest(root, request)" in text[name]
                for name in source_route_names
            )
        ),
        "creator-vtt-integrity": (
            "def _merge_creator_caption_lines(" in text["creator_vtt"]
            and "Exact adjacent duplicates are the same VTT render state" in text["creator_vtt"]
            and "Do not deduplicate against the whole cue" in text["creator_vtt"]
            and "parse_creator_vtt_preserving_text" in text["creator_vtt"]
            and "production.parse_manual_vtt = checked.parse_creator_vtt_preserving_text" in text["gemini"]
        ),
        "strict-translation-payload": (
            'POLICY = "strict-translation-payload-v1"' in text["strict_translation"]
            and "def validate_full(" in text["strict_translation"]
            and "def validate_subset(" in text["strict_translation"]
            and "Переводчик вернул повторяющийся ID" in text["strict_translation"]
            and "strict_translation_payload.validate_full(value, groups)" in text["translation"]
            and "strict_translation_payload.validate_subset(" in text["translation"]
            and "production._validate_translation_payload = strict_translation_payload.validate_full" in text["custom"]
            and '"source_language"' in text["translation"]
            and "с исходного языка на русский" in text["translation"]
            and "англоязычной" not in text["translation"].casefold()
            and "английской речью" not in text["translation"].casefold()
            and "production.acquire_transcript = _acquire_transcript_with_actual_language" in text["gemini"]
        ),
        "direct-v3": 'POLICY = "voxcpm2-direct-max-quality-v3"' in text["io"],
        "native-16to48": (
            "EXPECTED_ENCODE_SR = 16000" in text["io"]
            and "EXPECTED_OUTPUT_SR = 48000" in text["io"]
            and "AudioVAE:" in text["cli"]
        ),
        "fingerprints": (
            '"reference_sha256"' in text["cli"]
            and '"model_config_sha256"' in text["cli"]
            and "render_contract_sha256" in text["core"]
            and "release_contract_sha256" in text["core"]
        ),
        "retry-badcase": (
            '"retry_badcase": True' in text["render"]
            and '"retry_badcase_max_times": 2' in text["render"]
        ),
        "voice-and-timbre": (
            "candidate_hard_ok" in text["cli"]
            and "_finite_voice_metric" in text["analysis"]
            and "math.isfinite(value)" in text["analysis"]
            and "voiced_ratio" in text["analysis"]
            and "spectral_similarity" in text["analysis"]
            and "HARD_SIMILARITY_FLOOR = 0.30" in text["timbre"]
            and "MAX_TIMBRE_PENALTY" in text["timbre"]
            and "not np.isfinite(candidate).all()" in text["timbre"]
        ),
        "source-prosody-ranking": (
            'POLICY = "source-prosody-candidate-ranking-v2"' in text["prosody"]
            and "def candidate_pitch_evidence_ok(" in text["prosody"]
            and "def source_prosody_penalty(" in text["prosody"]
            and "def _acceptable_candidates(" in text["cli"]
            and "and candidate_pitch_evidence_ok(item)" in text["cli"]
            and "source_prosody_penalty(candidate, segment)" in text["cli"]
            and '"expression": expression_signature' in text["cli"]
            and '"selected_raw_pitch_evidence_ok": True' in text["cli"]
            and '"selected_base_score"' in text["cli"]
            and '"selected_source_prosody_match"' in text["cli"]
            and '"schema_version": "5.5-direct-durable-seed-epochs"' in text["cli"]
            and 'if candidate.get("cadence_hard_ok") is False:' in text["prosody"]
            and "detect_late_broadband_tail(" in text["prosody"]
            and "rawPitch=" in text["cli"]
            and "srcF0×=" in text["cli"]
        ),
        "continuous-first-reference": (
            'POLICY = "continuous-clean-reference-v2"' in text["continuous_reference"]
            and '"single-continuous-window"' in text["continuous_reference"]
            and '"multi-window-fallback"' in text["continuous_reference"]
            and "MIN_SECONDS = 5.0" in text["continuous_reference"]
            and "MAX_SECONDS = 10.0" in text["continuous_reference"]
            and "MIN_VOICED_RATIO = 0.16" in text["continuous_reference"]
            and "MIN_ACTIVE_RATIO = 0.25" in text["continuous_reference"]
            and "MAX_INTERNAL_GAP = 0.85" in text["continuous_reference"]
            and "_report_has_usable_selection" in text["continuous_reference"]
            and all(
                "continuous_reference_policy.build_calm_references" in text[name]
                and "clean.build_calm_references(" not in text[name]
                for name in route_names
            )
        ),
        "transactional-reference-identity": (
            "MIN_REFERENCE_SECONDS = 5.0" in text["reference_gate"]
            and "MIN_IDENTITY_SPECTRAL_SIMILARITY = 0.55" in text["reference_gate"]
            and 'IDENTITY_POLICY = "calm-and-expressive-identity-v2"' in text["reference_gate"]
            and "def _valid_calm_reference(" in text["reference_gate"]
            and "def _restore(" in text["reference_gate"]
            and "identity_spectral_similarity" in text["reference_gate"]
            and all(
                "controlled_reference_gate.build_or_keep_calm" in text[name]
                and "identity_reference=extended" in text[name]
                and "expressive_continuity.build_controlled_expressive_reference(" not in text[name]
                for name in route_names
            )
        ),
        "natural-timing": (
            "MAX_TEMPO = 1.35" in text["io"]
            and "MAX_START_DELAY_MS = 1500" in text["io"]
            and "def _finite_float(" in text["io"]
            and "Эффективное пересечение" in text["io"]
            and "MAX_SECONDS = 5.4" in text["core"]
            and "Russian tokens preserved" in text["normalizer"]
            and "afade=t=in" in text["render"]
            and "afade=t=out" in text["render"]
        ),
        "clean-reference": (
            "REFERENCE_TAIL_SILENCE = 0.0" in text["io"]
            and '"denoise": False' in text["reference"]
            and "afftdn" not in text["reference"]
            and '"denoise": False' in text["continuous_reference"]
            and '"spectral_filter": False' in text["continuous_reference"]
            and "pre-model-reference-hard-floor-v1" in text["analysis"]
        ),
        "QA-v3": (
            'POLICY = "clean-expression-aware-qa-v3"' in text["qa"]
            and "_forced_russian_fallback" in text["qa"]
            and "confident_foreign_block" in text["qa"]
            and "continuity_v45" in text["qa"]
            and "def _voice_limits(" in text["qa"]
            and 'POLICY: Final = "russian-spoken-numbers-v2"' in text["numeric"]
            and "def numeric_anchor_groups(" in text["numeric"]
            and 'NUMERIC_SEMANTIC_POLICY = "wetext-aligned-exact-numeric-anchors-v2"' in text["qa"]
            and "def _numeric_anchor_evidence(" in text["qa"]
            and "numeric_anchors_passed" in text["qa"]
        ),
        "same-direct-cli": (
            "from tools.voxcpm2 import direct_max_quality_cli as _direct_cli"
            in text["stable_cli"]
            and "main = _direct_cli.main" in text["stable_cli"]
            and "backend.build_renderer_command(" in text["core"]
            and "backend.build_master_command(" in text["core"]
            and "get_backend(" in text["core"]
        ),
        "no-wrapper": (
            "runpy.run_path" not in renderer_text
            and "class _SubprocessProxy" not in renderer_text
            and "semantic_tts_guard" not in renderer_text
            and "controlled_reference_gate" not in renderer_text
            and "continuous_reference_policy" not in renderer_text
            and '"wrapper_count": 0' in text["core"]
        ),
        "expression": (
            'POLICY = "source-guided-expression-v2"' in text["expression"]
            and "def _smooth(" in text["expression"]
            and "def plan_json(" in text["expression"]
            and "def _expressive_candidates(" in text["expression"]
            and "build_controlled_expressive_reference" in text["expression"]
        ),
        "translation-v2-bounded-gemini": (
            'POLICY = "expressive-spoken-translation-v2"' in text["translation"]
            and '_PROGRESS_PREFIX = "DUB_PROGRESS "' in text["translation"]
            and "перевод 1/3" in text["translation"]
            and "сверка 2/3" in text["translation"]
            and "редактура 3/3" in text["translation"]
            and "намеренные повторы" in text["translation"]
            and "риторические вопросы" in text["translation"]
            and "DUB_GEMINI_REQUEST_TIMEOUT_SEC" in text["gemini_runtime"]
            and "DUB_GEMINI_PASS_TIMEOUT_SEC" in text["gemini_runtime"]
            and "types.HttpOptions(timeout=" in text["gemini_runtime"]
            and "time.monotonic() + pass_timeout" in text["gemini_runtime"]
            and "remaining < _MIN_REQUEST_TIMEOUT_SECONDS" in text["gemini_runtime"]
            and "load_dotenv(override=False)" in text["gemini_runtime"]
            and "пробую следующий" in text["gemini_runtime"]
            and "production.translate_groups_max = expressive_translation.translate_groups" in text["gemini"]
        ),
        "clean-entrypoints": (
            "TTS guard disabled" in text["gemini"]
            and "TTS guard disabled" in text["direct"]
            and "TTS guard disabled" in text["custom"]
            and "force_fresh=repair_all" in text["repair"]
            and 'gemini_called": False' in text["repair"]
        ),
        "fixed-original-final-AAC-QA": (
            "linear=true" in text["master"]
            and "10.0 ** (float(target_tp) / 20.0)" in text["master"]
            and "level=false:latency=true" in text["master"]
            and "fixed-original-post-russian-master-v1" in text["master"]
            and '"post_mix_loudnorm": False' in text["master"]
            and '"post_mix_limiter": False' in text["master"]
            and "calibrate_russian_gain" in text["master"]
            and "verify_final_outputs" in text["master"]
            and "final_media_verification.json" in text["master"]
            and 'ORIGINAL_BED_POLICY = "post-aac-original-bed-regression-v1"' in text["media_qa"]
            and "def estimate_original_bed(" in text["media_qa"]
            and "def _estimate_alignment_lag(" in text["media_qa"]
            and "alignment_lag_ms" in text["media_qa"]
            and "local_spread_db" in text["media_qa"]
            and '"schema_version": "dub-final-media-qa-v5"' in text["media_qa"]
            and "Отчёт сохранён" in text["media_qa"]
            and "container_duration_delta_seconds" in text["media_qa"]
            and "audio_duration_delta_seconds" in text["media_qa"]
            and "av_start_delta_seconds" in text["media_qa"]
            and "AV_START_TOLERANCE_SECONDS = 0.05" in text["media_qa"]
            and "TRUE_PEAK_DELIVERY_CEILING_DBTP = -1.0" in text["media_qa"]
            and "EXPECTED_SAMPLE_RATE = 48_000" in text["media_qa"]
            and "MASTER_I = -16.0" in text["core"]
            and "MASTER_TP = -1.5" in text["core"]
        ),
        "editable-progress": (
            "edit_message_text" in text["runtime"]
            and "dub_progress_message_v1" in text["runtime"]
            and "_finalize_progress_card" in text["runtime"]
            and "dub_progress_updates" not in text["runtime"]
        ),
        "worker-v45": (
            'dub-worker-quality-v4.5' in text["runtime"]
            and 'dub-worker-quality-v4.5' in text["worker"]
            and "_progress_from_line_v44" in text["worker"]
            and "_recover_abandoned_with_terminal_events" in text["worker"]
            and "_FINAL_JOB_STATES" in text["worker"]
            and "status in _FINAL_JOB_STATES" in text["worker"]
            and 'return current, ""' in text["worker"]
        ),
        "single-title-policy": ("def canonical_media_title(" in text["title"] and "def canonical_delivery_filename(" in text["title"]),
    }
    failed = [name for name, ok in contracts.items() if not ok]
    return not failed, (
        "все контракты активны" if not failed else "не прошли: " + ", ".join(failed)
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
                "tools.voxcpm2.generic_clean_gemini_runtime" in gemini_text
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
