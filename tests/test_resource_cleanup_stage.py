"""Behavioral contracts for the extracted pipeline ``finally`` teardown."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from pipelines.resource_cleanup_stage import cleanup_pipeline_resources


class _Task:
    def __init__(self, events, *, done=False, await_error=None, cancel_error=None):
        self.events = events
        self._done = done
        self.await_error = await_error
        self.cancel_error = cancel_error

    def done(self):
        self.events.append(("done", None))
        return self._done

    def cancel(self):
        self.events.append(("cancel", None))
        if self.cancel_error is not None:
            raise self.cancel_error

    def __await__(self):
        async def wait():
            self.events.append(("await", None))
            if self.await_error is not None:
                raise self.await_error

        return wait().__await__()


class _Files:
    def __init__(self, events, *, error=None):
        self.events = events
        self.error = error

    async def delete(self, *, name):
        self.events.append(("delete", name))
        if self.error is not None:
            raise self.error


class _Thumbnail:
    def __init__(self, events, *, error=None):
        self.events = events
        self.error = error

    def close(self):
        self.events.append(("close", None))
        if self.error is not None:
            raise self.error


def _client(events, *, error=None):
    return SimpleNamespace(aio=SimpleNamespace(files=_Files(events, error=error)))


def _run(**overrides):
    kwargs = {
        "live_dub_task": None,
        "workdir": None,
        "used_audio_part": None,
        "used_client": None,
        "thumbnail": None,
    }
    kwargs.update(overrides)
    return asyncio.run(cleanup_pipeline_resources(**kwargs))


def _kinds(events):
    return [event[0] for event in events]


def test_no_resources_is_a_noop():
    assert _run() is None


def test_completed_task_missing_workdir_and_nameless_audio_are_skipped(tmp_path):
    events = []

    assert (
        _run(
            live_dub_task=_Task(events, done=True),
            workdir=tmp_path / "missing",
            used_audio_part=object(),
            used_client=_client(events),
        )
        is None
    )

    assert _kinds(events) == ["done"]
    assert not (tmp_path / "missing").exists()


def test_cancelled_task_workdir_audio_part_and_thumbnail_are_cleaned(tmp_path):
    events = []
    workdir = tmp_path / "ld_work"
    workdir.mkdir()
    (workdir / "partial.mp4").write_bytes(b"partial")

    assert (
        _run(
            live_dub_task=_Task(events, await_error=asyncio.CancelledError()),
            workdir=workdir,
            used_audio_part=SimpleNamespace(name="files/audio"),
            used_client=_client(events),
            thumbnail=_Thumbnail(events),
        )
        is None
    )

    assert _kinds(events) == ["done", "cancel", "await", "delete", "close"]
    assert events[3][1] == "files/audio"
    assert not workdir.exists()


def test_cancelled_task_may_finish_normally():
    events = []

    assert _run(live_dub_task=_Task(events)) is None

    assert _kinds(events) == ["done", "cancel", "await"]


def test_task_runtime_error_after_cancel_is_suppressed():
    events = []

    assert _run(live_dub_task=_Task(events, await_error=RuntimeError("worker failed"))) is None

    assert _kinds(events) == ["done", "cancel", "await"]


def test_named_audio_part_without_client_is_not_deleted():
    events = []

    assert (
        _run(
            used_audio_part=SimpleNamespace(name="files/audio"),
            used_client=None,
            thumbnail=_Thumbnail(events),
        )
        is None
    )

    assert _kinds(events) == ["close"]


def test_gemini_delete_failure_is_suppressed_and_thumbnail_still_closes():
    events = []

    assert (
        _run(
            used_audio_part=SimpleNamespace(name="files/audio"),
            used_client=_client(events, error=RuntimeError("delete failed")),
            thumbnail=_Thumbnail(events),
        )
        is None
    )

    assert _kinds(events) == ["delete", "close"]


def test_workdir_inspection_failure_stops_remaining_teardown():
    events = []

    class BrokenWorkdir:
        def exists(self):
            events.append(("exists", None))
            raise OSError("workdir unavailable")

    with pytest.raises(OSError, match="workdir unavailable"):
        _run(
            workdir=BrokenWorkdir(),
            used_audio_part=SimpleNamespace(name="files/audio"),
            used_client=_client(events),
            thumbnail=_Thumbnail(events),
        )

    assert _kinds(events) == ["exists"]


def test_cancel_failure_stops_remaining_teardown(tmp_path):
    events = []
    workdir = tmp_path / "ld_work"
    workdir.mkdir()

    with pytest.raises(RuntimeError, match="cancel failed"):
        _run(
            live_dub_task=_Task(events, cancel_error=RuntimeError("cancel failed")),
            workdir=workdir,
            used_audio_part=SimpleNamespace(name="files/audio"),
            used_client=_client(events),
            thumbnail=_Thumbnail(events),
        )

    assert _kinds(events) == ["done", "cancel"]
    assert workdir.exists()


def test_thumbnail_close_failure_propagates():
    events = []

    with pytest.raises(RuntimeError, match="close failed"):
        _run(thumbnail=_Thumbnail(events, error=RuntimeError("close failed")))

    assert _kinds(events) == ["close"]
