#!/usr/bin/env python3
"""HTTP-эндпоинты живости: /health (совместимость), /healthz и /metrics.

``/health`` существовал и раньше, но отвечал двумя строками. Здесь проверяется,
что старый формат не сломан (на него могут смотреть внешние мониторы) и что
новые эндпоинты действительно отвечают и действительно называют причину.
"""
from __future__ import annotations

import json

import pytest

from core import runtime_status


@pytest.fixture(scope="module")
def client():
    from core.globals import flask_app

    flask_app.config.update(TESTING=True)
    return flask_app.test_client()


@pytest.fixture(autouse=True)
def _fresh_state():
    runtime_status.reset()
    yield
    runtime_status.reset()


@pytest.fixture
def healthy_sources(monkeypatch: pytest.MonkeyPatch):
    """Подменяем внешние факты: реальные БД и диск в юнит-тесте не нужны."""
    from services import health_report

    monkeypatch.setattr(health_report, "database_ok", lambda *_a, **_k: True)
    monkeypatch.setattr(health_report, "disk_free_gb", lambda *_a, **_k: 42.0)
    monkeypatch.setattr(health_report, "gemini_keys_configured", lambda: 2)
    runtime_status.record_update()
    runtime_status.record_maintenance(ok=True)
    return health_report


def test_legacy_health_keeps_its_contract(client) -> None:
    from core.globals import mark_bot_alive

    mark_bot_alive()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "OK"


def test_healthz_reports_details(client, healthy_sources) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = json.loads(response.get_data(as_text=True))
    assert payload["ok"] is True
    assert payload["problems"] == []
    assert payload["database"]["ok"] is True
    assert payload["disk_free_gb"] == 42.0
    assert "version" in payload
    assert "gpu_slots_limit" in payload["jobs"]


def test_healthz_fails_when_database_is_down(client, healthy_sources, monkeypatch) -> None:
    monkeypatch.setattr(healthy_sources, "database_ok", lambda *_a, **_k: False)
    response = client.get("/healthz")
    assert response.status_code == 503
    payload = json.loads(response.get_data(as_text=True))
    assert "database_unavailable" in payload["problems"]


def test_healthz_does_not_leak_paths_or_secrets(client, healthy_sources) -> None:
    body = client.get("/healthz").get_data(as_text=True)
    assert "bot_cache.db" not in body
    assert "C:\\" not in body
    assert "/home/" not in body


def test_metrics_are_served_as_prometheus_text(client, healthy_sources) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert response.mimetype == "text/plain"
    assert "mp3bot_up 1" in body
    assert "mp3bot_updates_total" in body
    assert "mp3bot_disk_free_bytes" in body


def test_metrics_ignore_unknown_states(client, healthy_sources, monkeypatch) -> None:
    """Даже при пустом снимке эндпоинт отвечает валидным текстом, а не 500."""
    monkeypatch.setattr(runtime_status, "snapshot", lambda now=None: {})
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "mp3bot_up 1" in response.get_data(as_text=True)
