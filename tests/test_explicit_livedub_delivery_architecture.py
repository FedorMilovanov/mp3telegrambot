from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_services_import_has_no_import_hook_or_install_side_effects():
    src = _source("services/__init__.py")
    assert "sys.meta_path" not in src
    assert "MetaPathFinder" not in src
    assert "install_" not in src


def test_manifest_uses_explicit_delivery_contract_not_telegram_patch_stack():
    src = _source("services/runtime_manifest.py")
    for feature in (
        "livedub-audio-companion",
        "livedub-audio-dedupe",
        "livedub-new-delivery-atomicity",
        "livedub-cached-delivery-atomicity",
        "livedub-deep-audit",
        "livedub-dual-audio-policy",
        "livedub-output-policy",
    ):
        assert f'"{feature}"' not in src
    assert '"livedub-delivery-contract"' in src
    assert '"pre-main-quality-policy"' in src


def test_pipeline_calls_explicit_delivery_transactions():
    src = _source("pipelines/main_pipeline.py")
    assert "deliver_new_companions(" in src
    assert "deliver_cached_companions(" in src
    assert "create_source_audio_deferral(" in src
    assert "format_video_caption(_publication_card" in src


def test_coordinator_does_not_patch_telegram_methods():
    tree = ast.parse(_source("services/livedub_delivery_coordinator.py"))
    forbidden = {"send_video", "send_audio", "send_message", "reply_audio"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setattr":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                assert node.args[1].value not in forbidden
    assert "sys.meta_path" not in _source("services/livedub_delivery_coordinator.py")


def test_companion_cache_and_clean_track_are_source_owned():
    companion = _source("services/livedub_audio_companion.py")
    assert "load_recoverable_cache(_cache_path())" in companion
    assert "save_recoverable_cache(_cache_path(), data)" in companion
    assert 'setattr(cls, "send_video"' not in companion
    quality = _source("services/livedub_audio_quality_guard.py")
    assert "mix.find_pro_tracks =" not in quality
    assert "companion._send_new_audio =" not in quality


def test_livedub_mix_probe_is_utf8_and_major_fix_does_not_truncate():
    src = _source("services/livedub_mix.py")
    probe = src[src.index("def probe_video_meta"):src.index("def make_video_thumbnail")]
    assert 'encoding="utf-8"' in probe
    assert 'errors="replace"' in probe
    fix = src[src.index("def extract_fix_intervals"):src.index("async def apply_qa_audio_fixes")]
    assert "break" not in fix
    assert "refusing a partial auto-fix" in fix


def test_gemini_semantic_config_is_source_owned_high_and_sampling_free():
    src = _source("core/globals.py")
    assert 'if model == "gemini-3.6-flash":' in src
    assert 'return "high"' in src
    legacy = src[src.index("def make_text_config("):src.index("# FIX #2:")]
    assert "make_text_config_smart(" in legacy
    assert "GenerateContentConfig(" not in legacy
    subtitles = _source("services/eng_subtitles.py")
    assert 'thinking_level="high"' in subtitles
    assert "temperature=0.2" not in subtitles
