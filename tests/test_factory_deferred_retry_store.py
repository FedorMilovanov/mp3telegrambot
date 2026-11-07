from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.shorts_factory_delivery_ledger import FactoryDeliveryJournal
from services.shorts_factory_retry_store import FactoryDeferredRetryStore


def _ledger(tmp_path: Path) -> FactoryDeliveryJournal:
    return FactoryDeliveryJournal(
        chat_id=5678,
        source_id="video42",
        source_url="https://youtube.com/watch?v=video42",
        path=tmp_path / "factory.sqlite3",
    )


def _store(tmp_path: Path) -> FactoryDeferredRetryStore:
    return FactoryDeferredRetryStore(tmp_path / "factory.sqlite3")


def _register(store: FactoryDeferredRetryStore, ledger: FactoryDeliveryJournal, now: float) -> dict[str, Any]:
    return store.record_real_overload(
        scope=ledger.scope,
        chat_id=5678,
        user_id=1337,
        source_id="video42",
        source_url="https://youtube.com/watch?v=video42",
        stage="HIGH pass 1/3",
        actual_requests=5,
        now=now,
    )


def test_three_total_real_503_windows_are_bounded_and_persistent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ledger = _ledger(tmp_path)
    first = _register(store, ledger, now=1000.0)
    assert first["status"] == "scheduled"
    assert first["windows_used"] == 1
    assert 1000 + 270 <= first["retry_after"] <= 1000 + 330
    assert _store(tmp_path).claim_due(now=first["retry_after"] - 0.01) is None

    second_run = _store(tmp_path).claim_due(now=first["retry_after"])
    assert second_run is not None
    assert second_run.windows_used == 1
    assert _store(tmp_path).claim_due(now=first["retry_after"]) is None

    second = _register(store, ledger, now=first["retry_after"] + 0.1)
    assert second["status"] == "scheduled"
    assert second["windows_used"] == 2
    assert second["retry_after"] > first["retry_after"] + 0.1 + 800
    third_run = store.claim_due(now=second["retry_after"])
    assert third_run is not None
    third = _register(store, ledger, now=second["retry_after"] + 0.1)
    assert third["windows_used"] == 3
    assert third["status"] == "exhausted"
    assert third["retry_after"] == 0
    assert store.claim_due(now=second["retry_after"] + 10000) is None


def test_no_scheduling_for_forged_or_local_zero_request_rejection(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ledger = _ledger(tmp_path)
    with pytest.raises(ValueError, match="real provider"):
        store.record_real_overload(
            scope=ledger.scope,
            chat_id=5678,
            user_id=1337,
            source_id="video42",
            source_url="https://youtube.com/watch?v=video42",
            stage="HIGH pass 1/3",
            actual_requests=0,
            now=1000,
        )
    assert store.status(ledger.scope) is None


def test_user_cancellation_disables_scheduled_retry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ledger = _ledger(tmp_path)
    schedule = _register(store, ledger, 1000)
    store.cancel(ledger.scope)
    assert store.status(ledger.scope)["status"] == "canceled"  # type: ignore[index]
    assert store.claim_due(now=schedule["retry_after"] + 1) is None
    assert _register(store, ledger, 2000)["status"] == "canceled"


def test_success_supersedes_pending_manual_job(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ledger = _ledger(tmp_path)
    _register(store, ledger, 1000)
    store.mark_completed(ledger.scope)
    row = store.status(ledger.scope)
    assert row is not None
    assert row["status"] == "done"
    assert store.claim_due(now=9999) is None


def test_expired_job_never_retries_forever(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ledger = _ledger(tmp_path)
    _register(store, ledger, 1000)
    assert store.claim_due(now=1000 + 6 * 3600 + 5) is None
    row = store.status(ledger.scope)
    assert row is not None
    assert row["status"] == "exhausted"


def test_concurrent_claims_are_single_owner(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ledger = _ledger(tmp_path)
    first = _register(store, ledger, 1000)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(pool.map(lambda _: _store(tmp_path).claim_due(now=first["retry_after"] + 1), range(4)))
    assert sum(x is not None for x in claimed) == 1


def test_active_factory_lease_postpones_claim_without_charging_window(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ledger = _ledger(tmp_path)
    first = _register(store, ledger, 1000)
    owner = ledger.acquire_job_lease()
    assert store.claim_due(now=first["retry_after"]) is None
    row = store.status(ledger.scope)
    assert row is not None
    assert row["windows_used"] == 1
    assert row["retry_after"] == first["retry_after"] + 60
    ledger.release_job_lease(owner)
    assert store.claim_due(now=row["retry_after"]) is not None


def test_uncertain_telegram_send_blocks_deferred_retry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ledger = _ledger(tmp_path)
    first = _register(store, ledger, 1000)

    async def no_receipt() -> Any:
        return SimpleNamespace(chat_id=5678, message_id=None)

    from services.shorts_factory_delivery_ledger import FactoryDeliveryReconciliationRequired

    with pytest.raises(FactoryDeliveryReconciliationRequired):
        asyncio.run(ledger.send_once("short:1", semantic={"start": 1}, sender=no_receipt))
    assert store.claim_due(now=first["retry_after"] + 1) is None
    row = store.status(ledger.scope)
    assert row is not None
    assert row["status"] == "blocked"


def test_crashed_running_job_needs_proven_stopped_before_requeue(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ledger = _ledger(tmp_path)
    first = _register(store, ledger, 1000)
    claimed = store.claim_due(now=first["retry_after"])
    assert claimed is not None
    with pytest.raises(ValueError, match="independent proof"):
        store.operator_reset_crashed_running(
            ledger.scope,
            expected_owner=claimed.worker_owner,
            expected_pid=claimed.worker_pid,
            verified_stopped=False,
            now=1400,
        )
    with pytest.raises(RuntimeError, match="owner/PID changed"):
        store.operator_reset_crashed_running(
            ledger.scope,
            expected_owner="incorrect-owner",
            expected_pid=claimed.worker_pid,
            verified_stopped=True,
            now=1400,
        )
    store.operator_reset_crashed_running(
        ledger.scope,
        expected_owner=claimed.worker_owner,
        expected_pid=claimed.worker_pid,
        verified_stopped=True,
        now=1400,
    )
    assert store.status(ledger.scope)["status"] == "scheduled"  # type: ignore[index]
    assert store.claim_due(now=1400) is not None


def test_crashed_running_job_with_uncertain_send_cannot_auto_resume(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ledger = _ledger(tmp_path)
    first = _register(store, ledger, 1000)
    claimed = store.claim_due(now=first["retry_after"])
    assert claimed is not None

    async def error() -> Any:
        raise TimeoutError("response unknown")

    from services.shorts_factory_delivery_ledger import FactoryDeliveryReconciliationRequired

    with pytest.raises(FactoryDeliveryReconciliationRequired):
        asyncio.run(ledger.send_once("editorial:pack", semantic={"v": 1}, sender=error))
    with pytest.raises(RuntimeError, match="Uncertain Telegram"):
        store.operator_reset_crashed_running(
            ledger.scope,
            expected_owner=claimed.worker_owner,
            expected_pid=claimed.worker_pid,
            verified_stopped=True,
            now=1400,
        )


def test_exact_job_provenance_is_not_silently_reused_for_another_user(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ledger = _ledger(tmp_path)
    _register(store, ledger, 1000)
    with pytest.raises(RuntimeError, match="provenance changed"):
        store.record_real_overload(
            scope=ledger.scope,
            chat_id=5678,
            user_id=9876,
            source_id="video42",
            source_url="https://youtube.com/watch?v=video42",
            stage="HIGH pass 1/3",
            actual_requests=5,
            now=1500,
        )


def test_operator_cli_reports_status_and_refuses_implicit_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json
    from tools.factory_retry_jobs import main

    ledger = _ledger(tmp_path)
    store = _store(tmp_path)
    _register(store, ledger, 1000)
    base = [
        "--db",
        str(store.path),
        "--chat-id",
        "5678",
        "--source-id",
        "video42",
        "--url",
        "https://youtu.be/video42",
    ]
    assert main([*base, "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["retry"]["status"] == "scheduled"
    assert status["scope"] == ledger.scope
    with pytest.raises(SystemExit, match="2"):
        main([*base, "cancel"])
    assert store.status(ledger.scope)["status"] == "scheduled"  # type: ignore[index]
    assert main([*base, "cancel", "--human-verified", "--apply"]) == 0
    assert store.status(ledger.scope)["status"] == "canceled"  # type: ignore[index]


def test_retry_operator_cli_cannot_reset_running_with_uncertain_send(tmp_path: Path) -> None:
    from tools.factory_retry_jobs import main
    from services.shorts_factory_delivery_ledger import FactoryDeliveryReconciliationRequired

    ledger = _ledger(tmp_path)
    store = _store(tmp_path)
    retry = _register(store, ledger, 1000)
    claimed = store.claim_due(now=retry["retry_after"])
    assert claimed is not None

    async def timeout() -> Any:
        raise TimeoutError("possibly sent")

    with pytest.raises(FactoryDeliveryReconciliationRequired):
        asyncio.run(ledger.send_once("short:1", semantic={"v": 1}, sender=timeout))
    base = [
        "--db",
        str(store.path),
        "--chat-id",
        "5678",
        "--source-id",
        "video42",
        "--url",
        "https://youtu.be/video42",
    ]
    with pytest.raises(RuntimeError, match="Uncertain Telegram"):
        main(
            [
                *base,
                "reset-running",
                "--owner",
                claimed.worker_owner,
                "--pid",
                str(claimed.worker_pid),
                "--human-verified",
                "--apply",
            ]
        )
    assert store.status(ledger.scope)["status"] == "running"  # type: ignore[index]
