from __future__ import annotations

from typing import Any

import pytest

from tools.voxcpm2 import clean_production_core as core


def test_delivery_failure_retries_and_returns_success_without_reraising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_render(*_args: Any, **_kwargs: Any) -> str:
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            core._LAST_CHILD_STDERR = (
                "Сегмент #19: нет ни одного hard-quality кандидата. "
                "Следующий повтор использует seed epoch 1."
            )
            raise RuntimeError("Прямой VoxCPM2 renderer завершился с кодом 1.")
        return "ready"

    monkeypatch.setattr(core, "_legacy_render_and_master", fake_render)
    monkeypatch.setattr(core, "MAX_AUTOMATIC_DELIVERY_RETRIES", 3)

    assert core.render_and_master(object()) == "ready"
    assert calls == [1, 2]


def test_non_delivery_failure_is_never_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_render(*_args: Any, **_kwargs: Any) -> None:
        calls.append(1)
        core._LAST_CHILD_STDERR = "ModuleNotFoundError: No module named 'tools'"
        raise RuntimeError("Прямой master завершился с кодом 1.")

    monkeypatch.setattr(core, "_legacy_render_and_master", fake_render)
    monkeypatch.setattr(core, "MAX_AUTOMATIC_DELIVERY_RETRIES", 5)

    with pytest.raises(RuntimeError, match="ModuleNotFoundError"):
        core.render_and_master(object())
    assert calls == [1]


def test_checkpointed_delivery_retry_is_bounded_and_reports_deepest_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_render(*_args: Any, **_kwargs: Any) -> None:
        calls.append(1)
        core._LAST_CHILD_STDERR = (
            "Финальная AAC-дорожка не прошла ending/tail QA; "
            "последняя реплика переведена на новый seed epoch."
        )
        raise RuntimeError("Прямой master завершился с кодом 1.")

    monkeypatch.setattr(core, "_legacy_render_and_master", fake_render)
    monkeypatch.setattr(core, "MAX_AUTOMATIC_DELIVERY_RETRIES", 2)

    with pytest.raises(RuntimeError, match="Финальная AAC-дорожка"):
        core.render_and_master(object())
    assert len(calls) == 3


def test_retry_classifier_is_narrow_and_delivery_specific() -> None:
    assert core.DELIVERY_RETRY_POLICY == "bounded-checkpointed-delivery-retry-v1"
    assert core._retryable_delivery_failure("linked_phrase_gap; seed epochs") is True
    assert core._retryable_delivery_failure("late_broadband_burst") is True
    assert core._retryable_delivery_failure("HTTP 403 при скачивании source") is False
    assert core._retryable_delivery_failure("не найден ffmpeg") is False
