"""Regression contracts for Gemini quality policy and explicit LiveDub delivery."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import livedub_delivery_coordinator as delivery
from services import livedub_quality_runtime as runtime


def _max_policy_source() -> str:
    return Path("services/gemini_max_quality.py").read_text(encoding="utf-8")


def test_pre_main_manifest_owns_policy_before_core_clients() -> None:
    package = Path("services/__init__.py").read_text(encoding="utf-8")
    manifest = Path("services/runtime_manifest.py").read_text(encoding="utf-8")
    owner = Path("services/pre_main_policy.py").read_text(encoding="utf-8")
    assert "configure_gemini_policy()" not in package
    assert '"pre-main-quality-policy"' in manifest
    assert '"services.pre_main_policy"' in manifest
    assert owner.index("configure_max_quality_env()") < owner.index("configure_gemini_policy()")
    assert "configure_gemini_network()" in owner


def test_production_policy_keeps_semantic_work_on_37_high() -> None:
    runtime.configure_gemini_policy()
    for name in ("GEMINI_MODEL", "GEMINI_MAX_MODEL", "LIVEDUB_INFO_MODEL", "LIVEDUB_QUICK_QA_MODEL", "LIVEDUB_LONG_QA_MODEL", "LIVEDUB_QA_VERIFY_MODEL"):
        assert runtime.os.environ[name] == "gemini-3.7-flash"
    for name in ("GEMINI_FORCE_THINKING_LEVEL", "LIVEDUB_INFO_THINKING", "LIVEDUB_QUICK_QA_THINKING", "LIVEDUB_LONG_QA_THINKING", "LIVEDUB_QA_VERIFY_THINKING"):
        assert runtime.os.environ[name] == "high"
    assert runtime.os.environ["LIVEDUB_INFO_FALLBACK_MODELS"] == ""
    assert runtime.os.environ["LIVEDUB_PUBLICATION_FALLBACK_MODELS"] == ""


def test_utility_route_is_lite_only_without_semantic_fallback() -> None:
    runtime.configure_gemini_policy()
    assert runtime.os.environ["GEMINI_LIGHT_MODEL"] == "gemini-3.5-flash-lite"
    assert runtime.os.environ["GEMINI_LIGHT_FALLBACK_MODELS"] == ""
    assert runtime.os.environ["GEMINI_LIGHT_ALLOW_MAIN_FALLBACK"] == "0"
    assert "gemini-3.1" not in _max_policy_source()
    assert "gemini-2.5" not in _max_policy_source()


def test_user_visible_publication_uses_37_high_not_utility() -> None:
    publication = Path("services/livedub_publication_core.py").read_text(encoding="utf-8")
    assert '_PUBLICATION_MODEL = "gemini-3.7-flash"' in publication
    assert 'thinking_level="high"' in publication
    assert "GEMINI_LIGHT_MODEL" not in publication


def test_info_owner_refuses_stale_35_semantic_env(monkeypatch) -> None:
    import services.livedub_info as info
    monkeypatch.setenv("LIVEDUB_INFO_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("LIVEDUB_INFO_FALLBACK_MODELS", "gemini-3.5-flash")
    assert info.get_light_model() == "gemini-3.7-flash"
    assert info.get_light_model_fallbacks() == []


def test_info_card_uses_shared_bounded_transport_without_mutating_registry(monkeypatch) -> None:
    import services.livedub_info as info
    calls = []
    class Models:
        def __init__(self, name, fail): self.name, self.fail = name, fail
        async def generate_content(self, *, model, contents, config):
            del contents
            calls.append((self.name, model, config["thinking_level"]))
            if self.fail: raise RuntimeError("unavailable")
            return SimpleNamespace(text=json.dumps({"telegram_description":"Готово","youtube_title":"Название","youtube_description":"Описание","compact_subtitles":[],"hashtags":[],"key_theological_terms":[],"scripture_references":[]}, ensure_ascii=False))
    class Client:
        def __init__(self, name, fail): self.aio = SimpleNamespace(models=Models(name, fail))
    clients = [Client("first", True), Client("second", False)]
    monkeypatch.setattr(info, "GEMINI_CLIENTS", clients)
    monkeypatch.setattr(info, "get_light_model", lambda: "gemini-3.7-flash")
    monkeypatch.setattr(info, "get_light_model_fallbacks", lambda: [])
    monkeypatch.setattr(info, "make_text_config_smart", lambda **kwargs: kwargs)
    card = asyncio.run(info.build_livedub_info_card("Title", force=True))
    assert card and calls == [
        ("first", "gemini-3.7-flash", "high"),
        ("first", "gemini-3.7-flash", "high"),
        ("second", "gemini-3.7-flash", "high"),
    ]
    assert info.GEMINI_CLIENTS is clients


def test_singleflight_failure_releases_retry_key() -> None:
    delivery.reset_delivery_runtime_state()
    key = ("new", "failure-test-chat", "failure-test-reply", "failure-test-video")
    attempts = 0
    async def scenario():
        nonlocal attempts
        async def fail():
            nonlocal attempts; attempts += 1; raise RuntimeError("telegram send failed")
        async def succeed():
            nonlocal attempts; attempts += 1; return True
        with pytest.raises(RuntimeError, match="telegram send failed"):
            await delivery._singleflight(key, fail)
        assert key not in delivery._COMPANION_INFLIGHT
        assert key not in delivery._COMPANION_SENT
        assert await delivery._singleflight(key, succeed) is True
    asyncio.run(scenario())
    assert attempts == 2 and key in delivery._COMPANION_SENT


def test_singleflight_false_is_not_cached_and_concurrency_coalesces() -> None:
    delivery.reset_delivery_runtime_state()
    false_key = ("new", "chat", "reply", "false")
    async def false_once(): return False
    assert asyncio.run(delivery._singleflight(false_key, false_once)) is False
    assert false_key not in delivery._COMPANION_SENT

    key = ("new", "chat", "reply", "shared")
    attempts = 0
    async def scenario():
        nonlocal attempts
        started, release = asyncio.Event(), asyncio.Event()
        async def sender():
            nonlocal attempts; attempts += 1; started.set(); await release.wait(); return True
        first = asyncio.create_task(delivery._singleflight(key, sender))
        await started.wait()
        second = asyncio.create_task(delivery._singleflight(key, sender))
        await asyncio.sleep(0)
        assert attempts == 1
        release.set()
        assert await asyncio.gather(first, second) == [True, True]
    asyncio.run(scenario())
    assert attempts == 1


def test_windows_media_probes_are_utf8_safe_at_source_owner() -> None:
    companion = Path("services/livedub_audio_companion.py").read_text(encoding="utf-8")
    mix = Path("services/livedub_mix.py").read_text(encoding="utf-8")
    assert '"encoding": "utf-8"' in companion and '"errors": "replace"' in companion
    assert '"encoding": "utf-8"' in mix and '"errors": "replace"' in mix