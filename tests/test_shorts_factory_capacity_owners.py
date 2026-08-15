from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import services.shorts_factory_capacity as capacity


class _HttpError(RuntimeError):
    def __init__(self, code: int, message: str = "") -> None:
        super().__init__(message or str(code))
        self.code = code


def test_factory_overload_classifier_is_source_owned():
    error = _HttpError(503, "503 UNAVAILABLE: high demand")
    assert capacity.factory_retryable_service_error(error) is True
    assert capacity.factory_overload_error(error) is True
    assert capacity.factory_retryable_service_error(ValueError("bad json")) is False


def test_factory_clients_disable_hidden_sdk_retries(monkeypatch):
    from core import globals as core_globals

    created = []

    class Retry:
        def __init__(self, *, attempts):
            self.attempts = attempts

    class Http:
        def __init__(self, *, timeout, retry_options):
            self.timeout = timeout
            self.retry_options = retry_options

    def client(**kwargs):
        created.append(kwargs)
        return kwargs

    monkeypatch.setattr(core_globals, "HAS_GEMINI", True)
    monkeypatch.setattr(core_globals, "genai", SimpleNamespace(Client=client))
    monkeypatch.setattr(
        core_globals,
        "types",
        SimpleNamespace(HttpRetryOptions=Retry, HttpOptions=Http),
    )
    monkeypatch.setattr(core_globals, "GEMINI_API_KEY", "k1")
    monkeypatch.setattr(core_globals, "GEMINI_API_KEY_2", "k2")
    monkeypatch.setattr(core_globals, "GEMINI_API_KEY_3", "")
    monkeypatch.setattr(core_globals, "GEMINI_API_KEY_4", "")

    clients = capacity.factory_gemini_clients()
    assert len(clients) == 2
    assert [item["api_key"] for item in created] == ["k1", "k2"]
    assert all(item["http_options"].timeout == 900_000 for item in created)
    assert all(item["http_options"].retry_options.attempts == 1 for item in created)


def test_editorial_workflow_has_one_source_runner_without_context_bridge():
    runner = Path("services/translation_editorial_runner.py").read_text(encoding="utf-8")
    dispatcher = Path("pipelines/video_dispatch.py").read_text(encoding="utf-8")
    factory_pipeline = Path("pipelines/shorts_factory.py").read_text(encoding="utf-8")

    assert "ContextVar" not in runner
    assert "JOB_STATE" not in runner
    assert "mark_factory_analysis_audio_skipped" not in runner
    assert "services.translation_editorial_runner" in dispatcher
    assert "prepare_factory_editorial_review(" in factory_pipeline
    assert "shorts_factory_editorial_bridge" not in dispatcher
