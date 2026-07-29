from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from handlers import dub_audio_repair as handler


def _patch_project_paths(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(handler._legacy, "_project_root", lambda _project_id: root)
    monkeypatch.setattr(
        handler._legacy,
        "_segments_path",
        lambda _project_id: root / "segments_ru_final.json",
    )


def test_handler_loader_rejects_ambiguous_segment_ids(monkeypatch, tmp_path: Path) -> None:
    _patch_project_paths(monkeypatch, tmp_path)
    (tmp_path / "segments_ru_final.json").write_text(
        json.dumps(
            [
                {
                    "id": True,
                    "start": 0.0,
                    "end": 1.0,
                    "start_delay_ms": 0,
                    "text": "Реплика",
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="bool"):
        handler.load_repair_segments("project-1")


def test_handler_writer_records_exact_segments_hash(monkeypatch, tmp_path: Path) -> None:
    _patch_project_paths(monkeypatch, tmp_path)
    segments_path = tmp_path / "segments_ru_final.json"
    segments = [
        {
            "id": 1,
            "start": 0.0,
            "end": 1.0,
            "start_delay_ms": 0,
            "text": "Реплика",
        }
    ]
    segments_path.write_text(
        json.dumps(segments, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(handler._legacy, "utc_now", lambda: "2026-07-30T00:00:00Z")

    path = handler._write_repair_request(
        {"id": "project-1"},
        segments,
        [1],
        requested_by=123,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["project_id"] == "project-1"
    assert payload["segment_ids"] == [1]
    assert payload["repair_all"] is True
    assert payload["requested_by"] == 123
    assert payload["segments_sha256"] == handler._legacy.hashlib.sha256(
        segments_path.read_bytes()
    ).hexdigest()
    assert not list((tmp_path / "input").glob("audio_repair.json.tmp.*"))


@pytest.mark.parametrize(
    "selected",
    [[True], [1.5], [1, 1], [2]],
)
def test_handler_writer_rejects_ambiguous_selection(
    monkeypatch,
    tmp_path: Path,
    selected,
) -> None:
    _patch_project_paths(monkeypatch, tmp_path)
    segments = [
        {
            "id": 1,
            "start": 0.0,
            "end": 1.0,
            "start_delay_ms": 0,
            "text": "Реплика",
        }
    ]
    (tmp_path / "segments_ru_final.json").write_text(
        json.dumps(segments, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError):
        handler._write_repair_request(
            {"id": "project-1"},
            segments,
            selected,
            requested_by=123,
        )


def test_cross_process_lock_is_atomic_and_cleaned(monkeypatch, tmp_path: Path) -> None:
    lock_path = tmp_path / ".dubfix.request.lock"
    monkeypatch.setattr(handler, "_process_lock_path", lambda: lock_path)

    with handler._dubfix_process_lock():
        assert lock_path.is_file()
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
        with pytest.raises(RuntimeError, match="Другой процесс"):
            with handler._dubfix_process_lock():
                raise AssertionError("nested lock must not be acquired")
    assert not lock_path.exists()

    with pytest.raises(ValueError):
        with handler._dubfix_process_lock():
            raise ValueError("simulated command failure")
    assert not lock_path.exists()


def test_only_stale_process_lock_is_recovered(monkeypatch, tmp_path: Path) -> None:
    lock_path = tmp_path / ".dubfix.request.lock"
    lock_path.write_text("stale", encoding="utf-8")
    old = time.time() - handler._DUBFIX_PROCESS_LOCK_STALE_SECONDS - 10
    os.utime(lock_path, (old, old))
    monkeypatch.setattr(handler, "_process_lock_path", lambda: lock_path)

    with handler._dubfix_process_lock():
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
    assert not lock_path.exists()


def test_concurrent_dubfix_commands_are_serialized(
    monkeypatch,
    tmp_path: Path,
) -> None:
    active = 0
    maximum_active = 0
    order: list[str] = []
    monkeypatch.setattr(
        handler,
        "_process_lock_path",
        lambda: tmp_path / ".dubfix.request.lock",
    )

    async def fake_command(update, _context) -> None:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        order.append(f"start-{update.name}")
        await asyncio.sleep(0)
        order.append(f"end-{update.name}")
        active -= 1

    monkeypatch.setattr(handler, "_legacy_dubfix_command", fake_command)

    async def run() -> None:
        await asyncio.gather(
            handler.dubfix_command(SimpleNamespace(name="a"), object()),
            handler.dubfix_command(SimpleNamespace(name="b"), object()),
        )

    asyncio.run(run())
    assert maximum_active == 1
    assert order in (
        ["start-a", "end-a", "start-b", "end-b"],
        ["start-b", "end-b", "start-a", "end-a"],
    )
    assert not (tmp_path / ".dubfix.request.lock").exists()


def test_handler_facade_patches_legacy_registration_target() -> None:
    assert Path(handler.__file__).name == "__init__.py"
    assert handler._legacy.load_repair_segments is handler.load_repair_segments
    assert handler._legacy._write_repair_request is handler._write_repair_request
    assert handler._legacy.dubfix_command is handler.dubfix_command
