import asyncio
from pathlib import Path

import pytest

from pipelines import clips


def test_clips_candidate_budget_defaults_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLIPS_CANDIDATE_BUDGET_SECONDS", raising=False)
    assert clips._clips_candidate_budget_seconds() == 90.0

    monkeypatch.setenv("CLIPS_CANDIDATE_BUDGET_SECONDS", "not-a-number")
    assert clips._clips_candidate_budget_seconds() == 90.0

    monkeypatch.setenv("CLIPS_CANDIDATE_BUDGET_SECONDS", "1")
    assert clips._clips_candidate_budget_seconds() == 15.0

    monkeypatch.setenv("CLIPS_CANDIDATE_BUDGET_SECONDS", "900")
    assert clips._clips_candidate_budget_seconds() == 300.0


@pytest.mark.asyncio
async def test_optional_clips_timeout_returns_without_user_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_candidates(**_kwargs):
        started.set()
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.set()

    class Message:
        def __init__(self) -> None:
            self.replies: list[str] = []

        async def reply_text(self, text: str) -> None:
            self.replies.append(text)

    class Update:
        message = Message()

    mp3_path = tmp_path / "audio.mp3"
    mp3_path.write_bytes(b"audio")
    update = Update()

    monkeypatch.setattr(clips, "create_clips_candidates", slow_candidates)
    monkeypatch.setattr(clips, "_clips_candidate_budget_seconds", lambda: 0.01)

    await clips.process_and_send_clips(
        url="https://www.youtube.com/watch?v=test",
        media_id="abc",
        mp3_path=mp3_path,
        title="Title",
        performer="Author",
        duration=3600,
        ai_data={},
        update=update,
    )

    assert started.is_set()
    assert cancelled.is_set()
    assert update.message.replies == []
