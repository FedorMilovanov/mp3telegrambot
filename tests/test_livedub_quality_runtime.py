"""Regression contracts for Gemini/LiveDub Russian delivery."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import livedub_quality_runtime as runtime


def _runtime_source() -> str:
    return Path("services/livedub_quality_runtime.py").read_text(encoding="utf-8")


def _max_policy_source() -> str:
    return Path("services/gemini_max_quality.py").read_text(encoding="utf-8")


def _reset_audio_state() -> None:
    with runtime._AUDIO_LOCK:
        runtime._AUDIO_SENT.clear()
        runtime._AUDIO_INFLIGHT.clear()


def test_services_package_configures_policy_and_proxy_before_clients():
    package = Path("services/__init__.py").read_text(encoding="utf-8")
    assert "configure_gemini_policy" in package
    assert "configure_max_quality_env" in package
    assert package.index("configure_max_quality_env()") < package.index(
        "configure_gemini_policy()"
    )
    assert "configure_gemini_network" in package
    assert "services.livedub_audio_dedupe" in package
    assert "install_livedub_quality_runtime" in package


def test_production_policy_keeps_heavy_work_on_36_high():
    src = _max_policy_source()
    assert '_HEAVY_MODEL = "gemini-3.6-flash"' in src
    assert 'os.environ[name] = _HEAVY_MODEL' in src
    assert 'os.environ["LIVEDUB_INFO_FALLBACK_MODELS"] = ""' in src
    assert 'os.environ["GEMINI_FORCE_THINKING_LEVEL"] = "high"' in src


def test_light_work_uses_35_family_and_never_31_or_2x():
    src = _max_policy_source()
    assert '_LIGHT_MODEL = "gemini-3.5-flash-lite"' in src
    assert '_LIGHT_FALLBACK_MODEL = "gemini-3.5-flash"' in src
    assert 'os.environ["GEMINI_LIGHT_ALLOW_MAIN_FALLBACK"] = "0"' in src
    assert "gemini-3.1" not in src
    assert "gemini-2.5" not in src


def test_policy_owner_forces_exact_semantic_and_utility_routes_even_standalone(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-experimental-stale")
    monkeypatch.setenv("GEMINI_MAX_MODEL", "gemini-3.5-flash")
    monkeypatch.setenv("LIVEDUB_INFO_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("LIVEDUB_QUICK_QA_MODEL", "gemini-3.5-flash")
    monkeypatch.setenv("LIVEDUB_LONG_QA_MODEL", "gemini-experimental-stale")
    monkeypatch.setenv("LIVEDUB_QA_VERIFY_MODEL", "gemini-3.5-flash")
    monkeypatch.setenv("LIVEDUB_INFO_FALLBACK_MODELS", "gemini-3.5-flash")
    monkeypatch.setenv("GEMINI_LIGHT_MODEL", "gemini-experimental-stale")
    monkeypatch.setenv("GEMINI_LIGHT_ALLOW_MAIN_FALLBACK", "1")

    runtime.configure_gemini_policy()

    for name in (
        "GEMINI_MODEL",
        "GEMINI_MAX_MODEL",
        "LIVEDUB_INFO_MODEL",
        "LIVEDUB_QUICK_QA_MODEL",
        "LIVEDUB_LONG_QA_MODEL",
        "LIVEDUB_QA_VERIFY_MODEL",
    ):
        assert runtime.os.environ[name] == "gemini-3.6-flash"
    for name in (
        "GEMINI_FORCE_THINKING_LEVEL",
        "LIVEDUB_INFO_THINKING",
        "LIVEDUB_QUICK_QA_THINKING",
        "LIVEDUB_LONG_QA_THINKING",
        "LIVEDUB_QA_VERIFY_THINKING",
    ):
        assert runtime.os.environ[name] == "high"
    assert runtime.os.environ["LIVEDUB_INFO_FALLBACK_MODELS"] == ""
    assert runtime.os.environ["LIVEDUB_PUBLICATION_FALLBACK_MODELS"] == ""
    assert runtime.os.environ["GEMINI_LIGHT_MODEL"] == "gemini-3.5-flash-lite"
    assert runtime.os.environ["GEMINI_LIGHT_FALLBACK_MODELS"] == "gemini-3.5-flash"
    assert runtime.os.environ["GEMINI_LIGHT_ALLOW_MAIN_FALLBACK"] == "0"


def test_user_visible_publication_uses_36_high_not_35_utility():
    policy = _max_policy_source()
    publication = Path("services/livedub_publication_core.py").read_text(
        encoding="utf-8"
    )
    resilience = Path("services/gemini36_factory_resilience.py").read_text(
        encoding="utf-8"
    )
    assert 'os.environ["GEMINI_LIGHT_MODEL"] = _LIGHT_MODEL' in policy
    assert 'os.environ["LIVEDUB_PUBLICATION_FALLBACK_MODELS"] = ""' in policy
    assert 'os.environ["LIVEDUB_PUBLICATION_ALLOW_STRONG_FALLBACK"] = "0"' in policy
    assert 'os.environ["GEMINI_LIGHT_ALLOW_MAIN_FALLBACK"] = "0"' in policy
    assert '_PUBLICATION_MODEL = "gemini-3.6-flash"' in publication
    assert 'thinking_level="high"' in publication
    assert "GEMINI_LIGHT_MODEL" not in publication
    assert "_verify_publication_quality_route" in resilience
    assert "publication.publication_models =" not in resilience


def test_gemini_31_is_not_an_active_project_fallback():
    policy = _max_policy_source()
    script = Path("scripts/migrate-gemini-36.ps1").read_text(encoding="utf-8")
    assert "gemini-3.1" not in policy
    assert "gemini-3.1" not in script


def test_dead_local_proxy_falls_back_to_system_tun():
    src = _runtime_source()
    assert "_proxy_reachable" in src
    assert "_clear_dead_proxy" in src
    assert "system TUN (local proxy" in src


def test_yandex_tts_fallback_remains_explicit_opt_in():
    src = _runtime_source()
    assert 'os.environ.setdefault("LIVEDUB_TTS_FALLBACK", "1")' not in src
    mix = Path("services/livedub_mix.py").read_text(encoding="utf-8")
    assert ".voice_style_tts" in mix
    assert 'os.getenv("LIVEDUB_TTS_FALLBACK", "0")' in mix
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "LIVEDUB_TTS_FALLBACK=0" in env
    assert "LIVEDUB_TTS_FALLBACK=1" not in env


def test_project_obsolete_models_remain_named_for_diagnostics_only():
    src = _runtime_source()
    assert "_RETIRED_MODELS" in src
    assert '"gemini-3.1-flash-lite"' in src
    assert 'os.environ["GEMINI_LIGHT_MODEL"] = _LIGHT_MODEL' in src


def test_gemini_clients_are_never_rotated_globally():
    runtime_src = _runtime_source()
    info_src = Path("services/livedub_info.py").read_text(encoding="utf-8")
    assert "GEMINI_CLIENTS[:] =" not in runtime_src
    assert "_gemini_clients_snapshot" in info_src
    assert "client.aio.models.generate_content" in info_src
    assert "request-local client order" in info_src


def test_info_owner_refuses_stale_35_semantic_env(monkeypatch):
    import services.livedub_info as info

    monkeypatch.setenv("LIVEDUB_INFO_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("LIVEDUB_INFO_FALLBACK_MODELS", "gemini-3.5-flash")
    assert info.get_light_model() == "gemini-3.6-flash"
    assert info.get_light_model_fallbacks() == []


def test_info_card_tries_all_clients_with_high_config_without_mutating_registry(monkeypatch):
    import services.livedub_info as info

    calls: list[tuple[str, str]] = []
    configs: list[dict] = []

    class Models:
        def __init__(self, name: str, *, fail: bool) -> None:
            self.name = name
            self.fail = fail

        async def generate_content(self, *, model, contents, config):
            del contents
            calls.append((self.name, model))
            configs.append(config)
            if self.fail:
                raise RuntimeError(f"{self.name} unavailable")
            payload = {
                "telegram_description": "Готово",
                "youtube_title": "Название",
                "youtube_description": "Описание",
                "compact_subtitles": [],
                "hashtags": [],
                "key_theological_terms": [],
                "scripture_references": [],
            }
            return SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))

    class Client:
        def __init__(self, name: str, *, fail: bool) -> None:
            self.aio = SimpleNamespace(models=Models(name, fail=fail))

    clients = [Client("first", fail=True), Client("second", fail=False)]
    original_order = tuple(clients)
    monkeypatch.setattr(info, "GEMINI_CLIENTS", clients)
    monkeypatch.setattr(info, "get_light_model", lambda: "gemini-3.6-flash")
    monkeypatch.setattr(info, "get_light_model_fallbacks", lambda: [])
    monkeypatch.setattr(info, "make_text_config_smart", lambda **kwargs: kwargs)

    card = asyncio.run(info.build_livedub_info_card("Title", force=True))

    assert card is not None
    assert card["source"] == "gemini_quality"
    assert calls == [
        ("first", "gemini-3.6-flash"),
        ("second", "gemini-3.6-flash"),
    ]
    assert all(config["thinking_level"] == "high" for config in configs)
    assert all("temperature" not in config for config in configs)
    assert info.GEMINI_CLIENTS is clients
    assert tuple(info.GEMINI_CLIENTS) == original_order


def test_duplicate_delivery_uses_confirmed_success_cache():
    src = _runtime_source()
    assert "_AUDIO_SENT" in src
    assert "_AUDIO_INFLIGHT" in src
    assert "after confirmed success" in src
    assert "retry key released" in src
    assert "companion._send_new_audio = send_new_once" in src


def test_failed_audio_send_releases_retry_key():
    _reset_audio_state()
    key = ("new", "chat", "reply", "file", "video")
    attempts = 0

    async def scenario():
        nonlocal attempts

        async def fail():
            nonlocal attempts
            attempts += 1
            raise RuntimeError("telegram send failed")

        async def succeed():
            nonlocal attempts
            attempts += 1
            return True

        with pytest.raises(RuntimeError, match="telegram send failed"):
            await runtime._run_audio_once(key, "test", fail)
        assert key not in runtime._AUDIO_SENT
        assert key not in runtime._AUDIO_INFLIGHT
        assert await runtime._run_audio_once(key, "test", succeed) is True

    asyncio.run(scenario())
    assert attempts == 2
    assert key in runtime._AUDIO_SENT


def test_false_audio_result_is_not_cached_as_success():
    _reset_audio_state()
    key = ("new", "chat", "reply", "file", "video")
    attempts = 0

    async def scenario():
        nonlocal attempts

        async def return_false():
            nonlocal attempts
            attempts += 1
            return False

        assert await runtime._run_audio_once(key, "test", return_false) is False
        assert key not in runtime._AUDIO_SENT
        assert key not in runtime._AUDIO_INFLIGHT
        assert await runtime._run_audio_once(key, "test", return_false) is False

    asyncio.run(scenario())
    assert attempts == 2


def test_concurrent_audio_calls_share_the_real_result():
    _reset_audio_state()
    key = ("new", "chat", "reply", "file", "video")
    attempts = 0

    async def scenario():
        nonlocal attempts
        started = asyncio.Event()
        release = asyncio.Event()

        async def sender():
            nonlocal attempts
            attempts += 1
            started.set()
            await release.wait()
            return True

        first = asyncio.create_task(runtime._run_audio_once(key, "test", sender))
        await started.wait()
        second = asyncio.create_task(runtime._run_audio_once(key, "test", sender))
        await asyncio.sleep(0)
        assert attempts == 1
        release.set()
        assert await asyncio.gather(first, second) == [True, True]

    asyncio.run(scenario())
    assert attempts == 1


def test_windows_ffprobe_is_utf8_safe():
    src = _runtime_source()
    assert '"encoding": "utf-8"' in src
    assert '"errors": "replace"' in src
    assert "mix.probe_video_meta = utf8_probe" in src
