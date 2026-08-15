from __future__ import annotations

import importlib.util
import os
from pathlib import Path


_ENV_NAMES = (
    "LIVEDUB_QUICK_QA_MODEL",
    "LIVEDUB_LONG_QA_MODEL",
    "LIVEDUB_QA_VERIFY_MODEL",
    "LIVEDUB_QUICK_QA_THINKING",
    "LIVEDUB_LONG_QA_THINKING",
    "LIVEDUB_QA_VERIFY_THINKING",
    "LIVEDUB_QA_AUDIO_TRUST",
    "LIVEDUB_QA_CONFIRM_ISSUES",
    "LIVEDUB_QA_ALLOW_UNCONFIRMED",
)


def _load_module():
    path = Path(__file__).parents[1] / "services" / "gemini_qa_policy.py"
    spec = importlib.util.spec_from_file_location("gemini_qa_policy_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _clear(monkeypatch) -> None:
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_missing_and_weak_explicit_qa_models_are_upgraded(monkeypatch):
    policy = _load_module()
    _clear(monkeypatch)
    monkeypatch.setenv("LIVEDUB_QUICK_QA_MODEL", "gemini-3.1-flash-lite")
    monkeypatch.setenv("LIVEDUB_LONG_QA_MODEL", "gemini-3.5-flash")
    monkeypatch.setenv("LIVEDUB_QA_VERIFY_MODEL", "gemini-3.5-flash-lite")

    diagnostic = policy.configure_gemini_qa_policy()

    assert os.environ["LIVEDUB_QUICK_QA_MODEL"] == "gemini-3.6-flash"
    assert os.environ["LIVEDUB_LONG_QA_MODEL"] == "gemini-3.6-flash"
    assert os.environ["LIVEDUB_QA_VERIFY_MODEL"] == "gemini-3.6-flash"
    assert "migrated=LIVEDUB_QUICK_QA_MODEL,LIVEDUB_LONG_QA_MODEL,LIVEDUB_QA_VERIFY_MODEL" in diagnostic


def test_missing_qa_models_default_to_primary(monkeypatch):
    policy = _load_module()
    _clear(monkeypatch)

    policy.configure_gemini_qa_policy()

    assert {os.environ[name] for name in policy._QA_MODEL_ENV} == {"gemini-3.6-flash"}


def test_deliberate_custom_qa_model_is_preserved(monkeypatch):
    policy = _load_module()
    _clear(monkeypatch)
    monkeypatch.setenv("LIVEDUB_QUICK_QA_MODEL", "gemini-custom-audio-model")

    policy.configure_gemini_qa_policy()

    assert os.environ["LIVEDUB_QUICK_QA_MODEL"] == "gemini-custom-audio-model"
    assert os.environ["LIVEDUB_LONG_QA_MODEL"] == "gemini-3.6-flash"
    assert os.environ["LIVEDUB_QA_VERIFY_MODEL"] == "gemini-3.6-flash"


def test_translation_qa_thinking_is_always_high(monkeypatch):
    policy = _load_module()
    _clear(monkeypatch)
    monkeypatch.setenv("LIVEDUB_QUICK_QA_THINKING", "minimal")
    monkeypatch.setenv("LIVEDUB_LONG_QA_THINKING", "low")
    monkeypatch.setenv("LIVEDUB_QA_VERIFY_THINKING", "medium")

    policy.configure_gemini_qa_policy()

    assert {os.environ[name] for name in policy._QA_THINKING_ENV} == {"high"}


def test_stale_disabled_trust_and_confirmation_are_reenabled(monkeypatch):
    policy = _load_module()
    _clear(monkeypatch)
    monkeypatch.setenv("LIVEDUB_QA_AUDIO_TRUST", "0")
    monkeypatch.setenv("LIVEDUB_QA_CONFIRM_ISSUES", "0")

    diagnostic = policy.configure_gemini_qa_policy()

    assert os.environ["LIVEDUB_QA_AUDIO_TRUST"] == "1"
    assert os.environ["LIVEDUB_QA_CONFIRM_ISSUES"] == "1"
    assert "audio_trust=1" in diagnostic
    assert "confirm=1" in diagnostic


def test_explicit_emergency_escape_can_disable_confirmation(monkeypatch):
    policy = _load_module()
    _clear(monkeypatch)
    monkeypatch.setenv("LIVEDUB_QA_ALLOW_UNCONFIRMED", "1")
    monkeypatch.setenv("LIVEDUB_QA_AUDIO_TRUST", "0")
    monkeypatch.setenv("LIVEDUB_QA_CONFIRM_ISSUES", "0")

    diagnostic = policy.configure_gemini_qa_policy()

    assert os.environ["LIVEDUB_QA_AUDIO_TRUST"] == "0"
    assert os.environ["LIVEDUB_QA_CONFIRM_ISSUES"] == "0"
    assert "audio_trust=0" in diagnostic
    assert "confirm=0" in diagnostic


def test_manifest_runs_qa_policy_through_explicit_pre_main_owner():
    root = Path(__file__).parents[1]
    package_source = (root / "services" / "__init__.py").read_text(encoding="utf-8")
    manifest_source = (root / "services" / "runtime_manifest.py").read_text(encoding="utf-8")
    policy_source = (root / "services" / "pre_main_policy.py").read_text(encoding="utf-8")

    assert "configure_gemini_qa_policy()" not in package_source
    assert "sys.meta_path.insert" not in package_source
    assert "sys.meta_path.remove" not in package_source
    assert '"pre-main-quality-policy"' in manifest_source
    assert '"services.pre_main_policy"' in manifest_source
    assert 'RuntimePhase.PRE_MAIN' in manifest_source

    qa_call = policy_source.index("configure_gemini_qa_policy()")
    max_call = policy_source.index("configure_max_quality_env()")
    semantic_call = policy_source.index("configure_gemini_policy()")
    assert qa_call < max_call < semantic_call
    assert "from core.globals" not in policy_source
    assert "import core.globals" not in policy_source
