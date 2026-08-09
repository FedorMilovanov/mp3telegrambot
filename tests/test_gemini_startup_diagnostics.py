from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from services import gemini_startup_diagnostics as diagnostics


def _record(message: str, *, name: str = "main") -> logging.LogRecord:
    return logging.LogRecord(name, logging.WARNING, __file__, 1, message, (), None)


def test_filter_suppresses_only_stale_main_model_warnings():
    guard = diagnostics._LegacyGeminiModelFilter()
    assert not guard.filter(
        _record("⚠️  GEMINI_MODEL='%s' — модель не входит в список проверенных живых моделей.")
    )
    assert not guard.filter(
        _record("⚠️  GEMINI_MODEL='%s' — устарела и скоро будет отключена.")
    )
    assert guard.filter(_record("обычное предупреждение Local Bot API"))
    assert guard.filter(
        _record(
            "⚠️  GEMINI_MODEL='%s' — модель не входит в список проверенных живых моделей.",
            name="services.other",
        )
    )


def test_policy_diagnostic_accepts_current_primary_without_model_fallback():
    level, message = diagnostics.model_diagnostic("gemini-3.6-flash")
    assert level == logging.INFO
    assert "✅" in message
    assert "thinking=high" in message
    assert "model_fallbacks=disabled" in message
    assert "API-key rotation enabled" in message
    assert "gemini-3.5" not in message


def test_policy_diagnostic_rejects_every_non_36_model():
    for model in (
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "vendor-custom-model",
        "",
    ):
        level, message = diagnostics.model_diagnostic(model)
        assert level == logging.ERROR
        assert "gemini-3.6-flash" in message
        assert "forbids model downgrade" in message


def test_installer_wraps_run_once_and_preserves_result(monkeypatch, caplog):
    calls = 0

    async def original(value: int = 0):
        nonlocal calls
        calls += 1
        return value + 1

    logger = logging.getLogger("main")
    stub = SimpleNamespace(logger=logger, run_bot_async=original)
    monkeypatch.setattr(diagnostics, "_INSTALLED", False)
    monkeypatch.setattr(
        diagnostics,
        "_log_effective_model",
        lambda active_logger: active_logger.info("diagnostic-called"),
    )

    with caplog.at_level(logging.INFO, logger="main"):
        diagnostics.install_gemini_startup_diagnostics(stub)
        first_wrapper = stub.run_bot_async
        diagnostics.install_gemini_startup_diagnostics(stub)
        result = asyncio.run(stub.run_bot_async(4))

    assert result == 5
    assert calls == 1
    assert stub.run_bot_async is first_wrapper
    assert sum(record.message == "diagnostic-called" for record in caplog.records) == 1
    assert any(
        isinstance(item, diagnostics._LegacyGeminiModelFilter)
        for item in logger.filters
    )
    logger.filters[:] = [
        item
        for item in logger.filters
        if not isinstance(item, diagnostics._LegacyGeminiModelFilter)
    ]
