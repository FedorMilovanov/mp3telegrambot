#!/usr/bin/env python3
"""Production Local Bot API contract after the R24 patient-wait era.

The old optional transport lived inside ``main.py`` and could fall back to the
cloud after a short /getMe window. The validated entrypoint now installs a
required PRE_MAIN bootstrap: an already-warming local server is reused, a cold
server gets the full bounded readiness interval, and cloud media fallback is
forbidden. The historical main.py code remains only behind that mandatory gate
for compatibility and is not the production source of truth.
"""
from pathlib import Path

from services.runtime_manifest import DEFAULT_RUNTIME_FEATURES, RuntimePhase


REQUIRED = Path("services/local_botapi_required.py").read_text(encoding="utf-8")
ENTRYPOINT = Path("bot_new.py").read_text(encoding="utf-8")


def test_required_pre_main_gate_replaces_optional_patient_flag():
    feature = next(item for item in DEFAULT_RUNTIME_FEATURES if item.feature_id == "local-bot-api")
    assert feature.module == "services.local_botapi_required"
    assert feature.installer == "require_local_bot_api"
    assert feature.phase is RuntimePhase.PRE_MAIN
    assert feature.required is True
    assert ENTRYPOINT.index("bootstrap_pre_main()") < ENTRYPOINT.index("import main as _main_module")


def test_required_readiness_window_is_bounded_and_defaults_to_five_minutes():
    assert 'os.getenv("LOCAL_BOT_API_REQUIRED_TIMEOUT_SEC", "300")' in REQUIRED
    assert "max(60, min(value, 600))" in REQUIRED
    assert "time.monotonic() + timeout" in REQUIRED
    assert "сервер оставлен запущенным" in REQUIRED


def test_cloud_media_fallback_is_disabled_before_any_probe():
    function = REQUIRED[REQUIRED.index("def require_local_bot_api"):]
    assert function.index('os.environ["LOCAL_BOT_API_CLOUD_FALLBACK"] = "0"') < function.index("_probe_getme")
    assert function.index('os.environ["CLOUD_MEDIA_AUTO_COMPRESS"] = "0"') < function.index("_probe_getme")
    assert 'os.environ.pop("MP3BOT_EFFECTIVE_BOT_API", None)' in function
    assert 'MP3BOT_EFFECTIVE_BOT_API"] = "cloud"' not in REQUIRED


def test_warming_server_is_reused_and_cold_start_is_single_attempt():
    assert "local port already open; waiting without restart/logout" in REQUIRED
    assert "cold start: cloud logOut -> one local server start" in REQUIRED
    assert "_cloud_logout" in REQUIRED
    assert "_terminate_managed_server" in REQUIRED
    assert "_start_server" in REQUIRED


def test_env_documents_only_the_current_required_contract():
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "Локальный Telegram Bot API обязателен" in env
    assert "LOCAL_BOT_API_REQUIRED_TIMEOUT_SEC=300" in env
    assert "LOCAL_BOT_API_CLOUD_FALLBACK=0" in env
    assert "CLOUD_MEDIA_AUTO_COMPRESS=0" in env
    assert "LOCAL_BOT_API_WAIT_LOCAL=1" not in env
    assert "LOCAL_BOT_API_GETME_TIMEOUT_SEC" not in env
