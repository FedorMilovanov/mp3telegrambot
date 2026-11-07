from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.shorts_factory_delivery_ledger import (
    FactoryDeliveryJournal,
    FactoryDeliveryReconciliationRequired,
)


def _journal(tmp_path: Path, *, chat: int = 123, source: str = "id1") -> FactoryDeliveryJournal:
    return FactoryDeliveryJournal(
        chat_id=chat,
        source_id=source,
        source_url="https://www.youtube.com/watch?v=video1",
        path=tmp_path / "factory-delivery.sqlite3",
    )


def _ack(message_id: int = 501, chat_id: int = 123) -> Any:
    return SimpleNamespace(message_id=message_id, chat_id=chat_id)


def test_send_once_confirms_and_never_sends_same_artifact_twice(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    calls = 0

    async def send() -> Any:
        nonlocal calls
        calls += 1
        return _ack()

    async def scenario() -> None:
        first = await journal.send_once("short:1", semantic={"start": 10, "end": 70}, sender=send)
        second = await _journal(tmp_path).send_once("short:1", semantic={"start": 10, "end": 70}, sender=send)
        assert first.newly_delivered is True
        assert second.newly_delivered is False
        assert first.message_id == second.message_id == 501

    asyncio.run(scenario())
    assert calls == 1
    assert journal.status("short:1")["state"] == "delivered"  # type: ignore[index]


def test_network_timeout_freezes_artifact_across_new_invocation(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    calls = 0

    async def timeout() -> Any:
        nonlocal calls
        calls += 1
        raise TimeoutError("Telegram may already have accepted upload")

    async def scenario() -> None:
        with pytest.raises(FactoryDeliveryReconciliationRequired, match="unconfirmed"):
            await journal.send_once("long:1", semantic={"range": [5, 400]}, sender=timeout)
        with pytest.raises(FactoryDeliveryReconciliationRequired, match="do not retry"):
            await _journal(tmp_path).send_once("long:1", semantic={"range": [5, 400]}, sender=timeout)

    asyncio.run(scenario())
    assert calls == 1
    assert journal.status("long:1")["state"] == "sending"  # type: ignore[index]


def test_missing_receipt_never_becomes_delivered(tmp_path: Path) -> None:
    journal = _journal(tmp_path)

    async def no_receipt() -> Any:
        return SimpleNamespace(message_id=None, chat_id=123)

    async def scenario() -> None:
        with pytest.raises(FactoryDeliveryReconciliationRequired):
            await journal.send_once("doc:1", semantic={"name": "pack.zip"}, sender=no_receipt)
        with pytest.raises(FactoryDeliveryReconciliationRequired):
            await journal.send_once("doc:1", semantic={"name": "pack.zip"}, sender=no_receipt)

    asyncio.run(scenario())
    assert journal.status("doc:1")["state"] == "sending"  # type: ignore[index]


def test_wrong_chat_receipt_not_accepted(tmp_path: Path) -> None:
    journal = _journal(tmp_path)

    async def wrong_destination() -> Any:
        return _ack(message_id=77, chat_id=321)

    with pytest.raises(FactoryDeliveryReconciliationRequired):
        asyncio.run(journal.send_once("full:1", semantic={"duration": 99}, sender=wrong_destination))
    assert journal.status("full:1")["state"] == "sending"  # type: ignore[index]


def test_cancelled_send_stays_reserved(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    started = asyncio.Event()

    async def incomplete() -> Any:
        started.set()
        await asyncio.Event().wait()

    async def scenario() -> None:
        task = asyncio.create_task(journal.send_once("short:2", semantic={"index": 2}, sender=incomplete))
        await asyncio.wait_for(started.wait(), timeout=2.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(FactoryDeliveryReconciliationRequired):
            await journal.send_once("short:2", semantic={"index": 2}, sender=incomplete)

    asyncio.run(scenario())
    assert journal.status("short:2")["state"] == "sending"  # type: ignore[index]


def test_changed_semantics_require_reconciliation(tmp_path: Path) -> None:
    journal = _journal(tmp_path)

    async def sender() -> Any:
        return _ack()

    async def scenario() -> None:
        await journal.send_once("short:1", semantic={"start": 0, "end": 50}, sender=sender)
        with pytest.raises(FactoryDeliveryReconciliationRequired, match="fingerprint differs"):
            await journal.send_once("short:1", semantic={"start": 0, "end": 55}, sender=sender)

    asyncio.run(scenario())


def test_other_chats_sources_and_artifacts_remain_independent(tmp_path: Path) -> None:
    calls = 0

    async def sender() -> Any:
        nonlocal calls
        calls += 1
        return _ack(400 + calls, chat_id=123)

    async def sender_other_chat() -> Any:
        nonlocal calls
        calls += 1
        return _ack(400 + calls, chat_id=321)

    async def scenario() -> None:
        await _journal(tmp_path).send_once("short:1", semantic={"index": 1}, sender=sender)
        await _journal(tmp_path).send_once("short:2", semantic={"index": 2}, sender=sender)
        await _journal(tmp_path, source="id2").send_once("short:1", semantic={"index": 1}, sender=sender)
        await _journal(tmp_path, chat=321).send_once("short:1", semantic={"index": 1}, sender=sender_other_chat)

    asyncio.run(scenario())
    assert calls == 4


def test_two_concurrent_invocations_share_one_atomic_send_claim(tmp_path: Path) -> None:
    first = _journal(tmp_path)
    second = _journal(tmp_path)
    entered = asyncio.Event()
    finish = asyncio.Event()
    calls = 0

    async def send() -> Any:
        nonlocal calls
        calls += 1
        entered.set()
        await finish.wait()
        return _ack()

    async def scenario() -> None:
        first_task = asyncio.create_task(first.send_once("short:1", semantic={"index": 1}, sender=send))
        await asyncio.wait_for(entered.wait(), 2.0)
        with pytest.raises(FactoryDeliveryReconciliationRequired):
            await second.send_once("short:1", semantic={"index": 1}, sender=send)
        finish.set()
        await first_task
        previous = await second.send_once("short:1", semantic={"index": 1}, sender=send)
        assert previous.newly_delivered is False

    asyncio.run(scenario())
    assert calls == 1


def test_database_wal_and_confirmed_receipt_survive_reopen(tmp_path: Path) -> None:
    journal = _journal(tmp_path)

    async def send() -> Any:
        return _ack(message_id=990)

    asyncio.run(journal.send_once("short:1", semantic={"t": 1}, sender=send))
    with sqlite3.connect(journal.path) as conn:
        rows = conn.execute(
            "SELECT state,message_id,chat_id FROM factory_delivery WHERE scope=?",
            (journal.scope,),
        ).fetchall()
    assert rows == [("delivered", 990, 123)]
    assert _journal(tmp_path).status("short:1")["message_id"] == 990  # type: ignore[index]


def test_refuses_unknown_destination_before_writing_ledger(tmp_path: Path) -> None:
    msg = SimpleNamespace(chat_id=None)
    with pytest.raises(RuntimeError, match="cannot verify Telegram destination"):
        FactoryDeliveryJournal.for_update(SimpleNamespace(message=msg), media_id="x", source_url="url")
    assert not list(tmp_path.glob("*.sqlite3"))


def test_job_lease_prevents_two_concurrent_factory_processes(tmp_path: Path) -> None:
    from services.shorts_factory_delivery_ledger import FactoryJobAlreadyRunning

    first = _journal(tmp_path)
    second = _journal(tmp_path)
    owner = first.acquire_job_lease()
    with pytest.raises(FactoryJobAlreadyRunning, match="existing process"):
        second.acquire_job_lease()
    with pytest.raises(FactoryJobAlreadyRunning, match="owner changed"):
        first.release_job_lease("wrong-lease-owner")
    first.release_job_lease(owner)
    new_owner = second.acquire_job_lease()
    assert new_owner != owner
    second.release_job_lease(new_owner)


def test_crashed_factory_lease_never_stolen_without_reconciliation(tmp_path: Path) -> None:
    from services.shorts_factory_delivery_ledger import FactoryJobAlreadyRunning

    first = _journal(tmp_path)
    first.acquire_job_lease()  # simulate the process stopping without a finally block
    with pytest.raises(FactoryJobAlreadyRunning, match="operator recovery"):
        _journal(tmp_path).acquire_job_lease()


def test_manual_confirm_resolves_uncertain_receipt_without_new_send(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    called = 0

    async def timeout() -> Any:
        nonlocal called
        called += 1
        raise TimeoutError("reply lost")

    async def scenario() -> None:
        with pytest.raises(FactoryDeliveryReconciliationRequired):
            await journal.send_once("short:3", semantic={"index": 3}, sender=timeout)
        row = journal.status("short:3")
        assert row is not None
        fingerprint = row["fingerprint"]
        with pytest.raises(ValueError, match="verification"):
            journal.operator_reconcile_delivery(
                "short:3",
                expected_fingerprint=fingerprint,
                verified_message_id=1234,
                operator_verified=False,
            )
        with pytest.raises(FactoryDeliveryReconciliationRequired, match="changed"):
            journal.operator_reconcile_delivery(
                "short:3",
                expected_fingerprint="0" * 64,
                verified_message_id=1234,
                operator_verified=True,
            )
        journal.operator_reconcile_delivery(
            "short:3",
            expected_fingerprint=fingerprint,
            verified_message_id=1234,
            operator_verified=True,
        )
        confirmed = await journal.send_once("short:3", semantic={"index": 3}, sender=timeout)
        assert confirmed.newly_delivered is False
        assert confirmed.message_id == 1234

    asyncio.run(scenario())
    assert called == 1


def test_operator_verified_non_delivery_reopens_only_exact_artifact(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    called = 0

    async def unknown() -> Any:
        nonlocal called
        called += 1
        raise TimeoutError("unknown")

    async def good() -> Any:
        nonlocal called
        called += 1
        return _ack(670)

    async def scenario() -> None:
        with pytest.raises(FactoryDeliveryReconciliationRequired):
            await journal.send_once("long:2", semantic={"index": 2}, sender=unknown)
        prior = journal.status("long:2")
        assert prior is not None
        journal.operator_reconcile_delivery(
            "long:2",
            expected_fingerprint=prior["fingerprint"],
            verified_message_id=None,
            operator_verified=True,
        )
        assert journal.status("long:2") is None
        receipt = await journal.send_once("long:2", semantic={"index": 2}, sender=good)
        assert receipt.message_id == 670 and receipt.newly_delivered

    asyncio.run(scenario())
    assert called == 2


def test_manual_lease_recovery_checks_exact_owner_and_pid(tmp_path: Path) -> None:
    from services.shorts_factory_delivery_ledger import FactoryJobAlreadyRunning

    journal = _journal(tmp_path)
    journal.acquire_job_lease()
    row = journal.job_lease_status()
    assert row is not None
    assert isinstance(row["process_id"], int)
    with pytest.raises(ValueError):
        journal.operator_release_stale_job_lease(
            expected_owner=row["owner"],
            expected_process_id=row["process_id"],
            operator_verified_stopped=False,
        )
    with pytest.raises(FactoryJobAlreadyRunning):
        journal.operator_release_stale_job_lease(
            expected_owner="other",
            expected_process_id=row["process_id"],
            operator_verified_stopped=True,
        )
    with pytest.raises(FactoryJobAlreadyRunning):
        journal.operator_release_stale_job_lease(
            expected_owner=row["owner"],
            expected_process_id=row["process_id"] + 1,
            operator_verified_stopped=True,
        )
    journal.operator_release_stale_job_lease(
        expected_owner=row["owner"],
        expected_process_id=row["process_id"],
        operator_verified_stopped=True,
    )
    assert journal.job_lease_status() is None
    owner = journal.acquire_job_lease()
    journal.release_job_lease(owner)


def test_youtube_url_variants_share_one_source_and_chat_scope(tmp_path: Path) -> None:
    direct = _journal(tmp_path, source="video1")
    alias = FactoryDeliveryJournal(
        chat_id=123,
        source_id="video1",
        source_url="https://youtu.be/video1?feature=share",
        path=tmp_path / "factory-delivery.sqlite3",
    )
    assert direct.scope == alias.scope
    assert direct.scope != _journal(tmp_path, source="other").scope


def test_factory_media_fallback_id_is_stable_and_not_process_hash() -> None:
    from pipelines.shorts_factory import _media_id

    url = "https://youtu.be/stable?foo=bar"
    actual = _media_id({}, url)
    import hashlib

    assert actual == hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    assert _media_id({}, url) == actual


def test_operator_cli_is_readonly_by_default_and_requires_double_opt_in(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json
    from tools.factory_delivery_reconcile import main

    journal = _journal(tmp_path)
    owner = journal.acquire_job_lease()
    base = [
        "--db",
        str(journal.path),
        "--chat-id",
        "123",
        "--source-id",
        "id1",
        "--url",
        "https://www.youtube.com/watch?v=video1",
    ]
    assert main([*base, "status", "--artifact", "short:1"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["job_lease"]["owner"] == owner
    assert out["artifact"] is None
    with pytest.raises(SystemExit, match="2"):
        main([*base, "release-lease", "--owner", owner, "--pid", str(out["job_lease"]["process_id"])])
    assert journal.job_lease_status() is not None
    assert (
        main(
            [
                *base,
                "release-lease",
                "--owner",
                owner,
                "--pid",
                str(out["job_lease"]["process_id"]),
                "--human-verified",
                "--apply",
            ]
        )
        == 0
    )
    assert journal.job_lease_status() is None


def test_rendered_file_sha_changes_when_content_changes(tmp_path: Path) -> None:
    from services.shorts_factory_delivery_ledger import factory_delivery_media_sha256

    path = tmp_path / "delivered.mp4"
    path.write_bytes(b"version-one")

    async def scenario() -> None:
        first = await factory_delivery_media_sha256(path)
        await asyncio.to_thread(path.write_bytes, b"version-two")
        second = await factory_delivery_media_sha256(path)
        assert first != second

    asyncio.run(scenario())
