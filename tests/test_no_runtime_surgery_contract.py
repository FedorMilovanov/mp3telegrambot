from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CRITICAL_SURFACES = (
    "services/__init__.py",
    "services/runtime_manifest.py",
    "services/pre_main_policy.py",
    "services/gemini_max_quality.py",
    "services/polling_reliability_runtime.py",
    "services/restart_state_runtime.py",
    "services/bot_lifecycle.py",
    "main.py",
    "services/livedub_quality_runtime.py",
    "services/livedub_delivery_coordinator.py",
    "services/livedub_audio_companion.py",
    "services/livedub_audio_cache_recovery.py",
    "services/livedub_audio_quality_guard.py",
    "services/livedub_qa.py",
    "services/livedub_long_qa.py",
    "services/livedub_qa_trust.py",
    "services/livedub_qa_hardening.py",
    "services/livedub_ru_provenance.py",
    "services/study_synthesis_runtime.py",
    "services/study_synthesis_policy.py",
    "services/conspect_quality_contract.py",
    "services/conspect_audit_runtime.py",
    "core/study_quality.py",
    "core/structured_blocks.py",
    "core/content_audit.py",
    "pipelines/main_pipeline.py",
)

FORBIDDEN_TEXT = (
    "sys.meta_path.insert",
    "sys.meta_path.remove",
    "MetaPathFinder",
    "Bot.send_video =",
    "Bot.send_audio =",
    "Bot.send_message =",
    "ExtBot.send_video =",
    "Message.reply_audio =",
    "Updater.start_polling =",
    "Application.process_update =",
    "yandex.get_live_dub_audio =",
    "mix.find_pro_tracks =",
    ".run_translation_qa =",
    ".format_qa_report =",
    ".normalize_structured_block =",
    ".audit_expanded_sections =",
    ".STUDY_ANALYSIS_PROMPT =",
)

TELEGRAM_METHODS = {"send_video", "send_audio", "send_message", "reply_audio"}


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_critical_runtime_surfaces_have_no_import_or_assignment_surgery():
    for rel in CRITICAL_SURFACES:
        src = _source(rel)
        for token in FORBIDDEN_TEXT:
            assert token not in src, f"{rel} reintroduced runtime surgery: {token}"


def test_critical_runtime_surfaces_do_not_setattr_telegram_delivery_methods():
    for rel in CRITICAL_SURFACES:
        tree = ast.parse(_source(rel), filename=rel)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "setattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
            ):
                continue
            method = node.args[1].value
            assert method not in TELEGRAM_METHODS, (
                f"{rel} reintroduced Telegram delivery interception: {method}"
            )


def test_runtime_manifest_critical_routes_are_contracts_not_patch_stacks():
    src = _source("services/runtime_manifest.py")
    assert '"livedub-qa-contract"' in src
    assert '"livedub-delivery-contract"' in src
    assert '"pre-main-quality-policy"' in src
    for legacy in (
        "livedub-long-qa",
        "livedub-qa-trust",
        "livedub-ru-provenance",
        "livedub-qa-hardening",
        "livedub-output-policy",
        "livedub-publication",
        "livedub-audio-companion",
        "livedub-audio-dedupe",
        "livedub-new-delivery-atomicity",
        "livedub-cached-delivery-atomicity",
        "livedub-deep-audit",
        "livedub-dual-audio-policy",
        "conspect-quality-bootstrap",
    ):
        assert f'"{legacy}"' not in src
