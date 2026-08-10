"""Keep Factory orchestration timeout aligned with the production LiveDub source."""

import pipelines.shorts_factory as factory
from services.shorts_factory_source import _factory_livedub_timeout_seconds


def test_factory_timeout_defaults_match(monkeypatch):
    monkeypatch.delenv("SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC", raising=False)

    assert factory._factory_source_timeout_seconds() == 1800
    assert _factory_livedub_timeout_seconds() == 1800


def test_factory_timeout_cannot_cancel_before_production_floor(monkeypatch):
    monkeypatch.setenv("SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC", "600")

    assert factory._factory_source_timeout_seconds() == 1800
    assert _factory_livedub_timeout_seconds() == 1800


def test_factory_timeout_is_bounded_at_two_hours(monkeypatch):
    monkeypatch.setenv("SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC", "99999")

    assert factory._factory_source_timeout_seconds() == 7200
    assert _factory_livedub_timeout_seconds() == 7200


def test_factory_timeout_invalid_value_uses_production_default(monkeypatch):
    monkeypatch.setenv("SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC", "not-a-number")

    assert factory._factory_source_timeout_seconds() == 1800
    assert _factory_livedub_timeout_seconds() == 1800
