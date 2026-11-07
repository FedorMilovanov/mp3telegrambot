from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pipelines import shorts_factory as factory
from services import gemini_capacity_control
from services.shorts_factory_delivery_ledger import FactoryDeliveryJournal
from services.shorts_factory_provider_status import FactoryProviderOverloaded
from services.shorts_factory_retry_store import FactoryDeferredRetryStore


@pytest.mark.asyncio
async def test_real_provider_failure_persists_retry_before_returning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    journal_path = tmp_path / "factory.sqlite3"
    updates: list[str] = []
    audio = tmp_path / "audio.aac"
    audio.write_bytes(b"verified-analysis" * 100)

    async def metadata(url: str) -> dict[str, Any]:
        return {"id": "video444", "duration": 120, "title": "Sermon", "channel": "Speaker"}

    async def get_audio(*args: Any, **kwargs: Any) -> Path:
        return audio

    async def real_503(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise FactoryProviderOverloaded(
            "503 real HTTP requests exhausted",
            stage="Factory HIGH pass 1/3",
            actual_attempts=5,
        )

    def journal(cls: Any, update: Any, *, media_id: str, source_url: str) -> FactoryDeliveryJournal:
        return FactoryDeliveryJournal(
            chat_id=123,
            source_id=media_id,
            source_url=source_url,
            path=journal_path,
        )

    async def edit(text: str) -> None:
        updates.append(text)

    async def no_message(text: str, **kwargs: Any) -> Any:
        raise AssertionError("No unexpected user-visible sends")

    monkeypatch.setattr(factory, "enforce_factory_preflight", lambda: None)
    monkeypatch.setattr(factory, "_cleanup_expired_factory_sources", lambda: None)
    monkeypatch.setattr(factory, "_load_video_info", metadata)
    monkeypatch.setattr(factory, "_download_factory_audio", get_audio)
    monkeypatch.setattr(factory, "create_factory_plan", real_503)
    monkeypatch.setattr(factory, "_cleanup_factory_files", lambda **kwargs: None)
    monkeypatch.setattr(FactoryDeliveryJournal, "for_update", classmethod(journal))
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=456),
        message=SimpleNamespace(chat_id=123, reply_text=no_message),
    )
    status = SimpleNamespace(edit_text=edit)
    first = await factory.process_shorts_factory("https://www.youtube.com/watch?v=video444", update, status_msg=status)
    assert first is False
    scope = FactoryDeliveryJournal(
        chat_id=123,
        source_id="video444",
        source_url="https://youtu.be/video444",
        path=journal_path,
    ).scope
    store = FactoryDeferredRetryStore(journal_path)
    state = store.status(scope)
    assert state is not None
    assert state["status"] == "scheduled"
    assert state["windows_used"] == 1
    assert state["last_actual_requests"] == 5
    assert any("ограниченное возобновление" in message for message in updates)

    monkeypatch.setenv("SHORTS_FACTORY_DEFERRED_RETRY_ENABLED", "0")
    second = await factory.process_shorts_factory("https://www.youtube.com/watch?v=video444", update, status_msg=status)
    assert second is False
    unchanged = store.status(scope)
    assert unchanged is not None and unchanged["windows_used"] == 1
    assert any("очередь возобновления отключена" in message for message in updates)


@pytest.mark.asyncio
async def test_local_circuit_does_not_schedule_delayed_provider_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    journal_path = tmp_path / "factory.sqlite3"
    audio = tmp_path / "audio.aac"
    audio.write_bytes(b"verified-analysis" * 100)

    async def metadata(url: str) -> dict[str, Any]:
        return {"id": "video444", "duration": 120, "title": "Sermon"}

    async def get_audio(*args: Any, **kwargs: Any) -> Path:
        return audio

    async def local_circuit(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise gemini_capacity_control.GeminiCapacityCircuitOpen(
            "local 503 circuit",
            retry_after_seconds=60,
        )

    def journal(cls: Any, update: Any, *, media_id: str, source_url: str) -> FactoryDeliveryJournal:
        return FactoryDeliveryJournal(
            chat_id=123,
            source_id=media_id,
            source_url=source_url,
            path=journal_path,
        )

    async def status(text: str) -> None:
        pass

    monkeypatch.setattr(factory, "enforce_factory_preflight", lambda: None)
    monkeypatch.setattr(factory, "_cleanup_expired_factory_sources", lambda: None)
    monkeypatch.setattr(factory, "_load_video_info", metadata)
    monkeypatch.setattr(factory, "_download_factory_audio", get_audio)
    monkeypatch.setattr(factory, "create_factory_plan", local_circuit)
    monkeypatch.setattr(factory, "_cleanup_factory_files", lambda **kwargs: None)
    monkeypatch.setattr(FactoryDeliveryJournal, "for_update", classmethod(journal))
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=456),
        message=SimpleNamespace(chat_id=123, reply_text=status),
    )
    ok = await factory.process_shorts_factory(
        "https://youtube.com/watch?v=video444",
        update,
        status_msg=SimpleNamespace(edit_text=status),
    )
    assert ok is False
    scope = FactoryDeliveryJournal(
        chat_id=123,
        source_id="video444",
        source_url="https://youtu.be/video444",
        path=journal_path,
    ).scope
    assert FactoryDeferredRetryStore(journal_path).status(scope) is None
