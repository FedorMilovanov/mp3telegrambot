from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.shorts_factory_delivery_ledger import FactoryDeliveryJournal
from services.shorts_factory_provider_status import FactoryProviderOverloaded
from pipelines.factory_retry_runtime import (
    _FactoryScheduledMessage,
    _retry_update,
    _run_due_job,
    start_factory_retry_services,
    stop_factory_retry_services,
)
from services.shorts_factory_retry_store import FactoryDeferredRetryStore


def _ready_store(tmp_path: Path) -> tuple[FactoryDeferredRetryStore, Any]:
    journal = FactoryDeliveryJournal(
        chat_id=123,
        source_id="id22",
        source_url="https://youtu.be/id22",
        path=tmp_path / "delivery.sqlite3",
    )
    store = FactoryDeferredRetryStore(journal.path)
    schedule = store.record_real_overload(
        scope=journal.scope,
        chat_id=123,
        user_id=456,
        source_id="id22",
        source_url="https://youtu.be/id22",
        stage="HIGH pass 1/3",
        actual_requests=5,
        now=1000,
    )
    job = store.claim_due(now=schedule["retry_after"] + 0.1)
    assert job is not None
    return store, job


def test_provider_error_is_typed_and_does_not_claim_quota(tmp_path: Path) -> None:
    exc = FactoryProviderOverloaded("503 real provider", stage="HIGH pass 2/3", actual_attempts=5)
    assert isinstance(exc, RuntimeError)
    assert exc.stage == "HIGH pass 2/3"
    assert exc.actual_attempts == 5


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[str, int]] = []

    async def send_message(self, *, chat_id: int, text: str, **kwargs: Any) -> Any:
        self.messages.append(("text", chat_id))
        return SimpleNamespace(chat_id=chat_id, message_id=len(self.messages), text=text)

    async def send_video(self, *, chat_id: int, **kwargs: Any) -> Any:
        self.messages.append(("video", chat_id))
        return SimpleNamespace(chat_id=chat_id, message_id=len(self.messages))

    async def send_document(self, *, chat_id: int, **kwargs: Any) -> Any:
        self.messages.append(("document", chat_id))
        return SimpleNamespace(chat_id=chat_id, message_id=len(self.messages))


class FakeApp:
    def __init__(self) -> None:
        self.bot = FakeBot()
        self.bot_data: dict[str, Any] = {}
        self.calls = 0

    def create_task(self, coro: Any, *, name: str) -> asyncio.Task[Any]:
        self.calls += 1
        return asyncio.create_task(coro, name=name)


@pytest.mark.asyncio
async def test_real_bot_chat_adapter_returns_real_receipts(tmp_path: Path) -> None:
    store, job = _ready_store(tmp_path)
    bot = FakeBot()
    update = _retry_update(bot, job)
    assert update.effective_user.id == 456
    assert update.message.chat_id == 123
    await update.message.reply_text("test")
    video = await update.message.reply_video(video=b"mock")
    document = await update.message.reply_document(document=b"mock")
    assert (video.chat_id, video.message_id) == (123, 2)
    assert (document.chat_id, document.message_id) == (123, 3)
    assert bot.messages == [("text", 123), ("video", 123), ("document", 123)]
    assert isinstance(update.message, _FactoryScheduledMessage)


@pytest.mark.asyncio
async def test_due_worker_completes_without_duplicate_work(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from core import user_modes
    import pipelines.shorts_factory as factory

    store, job = _ready_store(tmp_path)
    app = FakeApp()
    calls: list[str] = []

    async def mode(user_id: int) -> str:
        assert user_id == 456
        return "shorts_max"

    async def process(url: str, update: Any, **kwargs: Any) -> bool:
        calls.append(url)
        await update.message.reply_text("processed")
        return True

    monkeypatch.setattr(user_modes, "get_user_mode", mode)
    monkeypatch.setattr(factory, "process_shorts_factory", process)
    await _run_due_job(app, store, job)
    assert calls == [job.source_url]
    status = store.status(job.scope)
    assert status is not None
    assert status["status"] == "done"
    assert app.bot.messages == [("text", 123)]


@pytest.mark.asyncio
async def test_second_real_503_preserves_scheduled_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from core import user_modes
    import pipelines.shorts_factory as factory

    store, job = _ready_store(tmp_path)
    app = FakeApp()

    async def mode(user_id: int) -> str:
        return "shorts_max"

    async def process(url: str, update: Any, **kwargs: Any) -> bool:
        store.record_real_overload(
            scope=job.scope,
            chat_id=job.chat_id,
            user_id=job.user_id,
            source_id=job.source_id,
            source_url=job.source_url,
            stage="HIGH pass 2/3",
            actual_requests=5,
            now=2000,
        )
        return False

    monkeypatch.setattr(user_modes, "get_user_mode", mode)
    monkeypatch.setattr(factory, "process_shorts_factory", process)
    await _run_due_job(app, store, job)
    row = store.status(job.scope)
    assert row is not None
    assert row["status"] == "scheduled"
    assert row["windows_used"] == 2


@pytest.mark.asyncio
async def test_nonprovider_failure_is_blocked_not_retried_forever(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from core import user_modes
    import pipelines.shorts_factory as factory

    store, job = _ready_store(tmp_path)
    app = FakeApp()

    async def mode(user_id: int) -> str:
        return "shorts_max"

    async def process(url: str, update: Any, **kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(user_modes, "get_user_mode", mode)
    monkeypatch.setattr(factory, "process_shorts_factory", process)
    await _run_due_job(app, store, job)
    row = store.status(job.scope)
    assert row is not None
    assert row["status"] == "blocked"


@pytest.mark.asyncio
async def test_mode_change_blocks_automatic_resume(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from core import user_modes
    import pipelines.shorts_factory as factory

    store, job = _ready_store(tmp_path)
    app = FakeApp()

    async def mode(user_id: int) -> str:
        return "rus"

    async def forbidden(url: str, update: Any, **kwargs: Any) -> bool:
        raise AssertionError("mode change must not launch video processing")

    monkeypatch.setattr(user_modes, "get_user_mode", mode)
    monkeypatch.setattr(factory, "process_shorts_factory", forbidden)
    await _run_due_job(app, store, job)
    row = store.status(job.scope)
    assert row is not None
    assert row["status"] == "blocked"


@pytest.mark.asyncio
async def test_scheduler_lifecycle_has_one_application_owned_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from pipelines import factory_retry_runtime as runtime

    monkeypatch.setenv("SHORTS_FACTORY_DEFERRED_RETRY_ENABLED", "1")
    monkeypatch.setattr(
        runtime, "FactoryDeferredRetryStore", lambda: FactoryDeferredRetryStore(tmp_path / "retry.sqlite3")
    )
    app = FakeApp()
    assert start_factory_retry_services(app) is True
    assert start_factory_retry_services(app) is True
    assert app.calls == 1
    await asyncio.sleep(0)
    await stop_factory_retry_services(app)
    assert "factory_deferred_retry_task" not in app.bot_data
    monkeypatch.setenv("SHORTS_FACTORY_DEFERRED_RETRY_ENABLED", "0")
    assert start_factory_retry_services(app) is False
    assert app.calls == 1
