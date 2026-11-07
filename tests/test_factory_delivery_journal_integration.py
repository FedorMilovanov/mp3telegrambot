from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services import shorts_factory_full_video as full_video
from services import translation_editorial_factory as editorial
from services.shorts_factory_delivery_ledger import (
    FactoryDeliveryJournal,
    FactoryDeliveryReconciliationRequired,
)


class TelegramMessage:
    def __init__(self, chat_id: int = 123) -> None:
        self.chat_id = chat_id
        self.video_ids: list[int] = []
        self.document_ids: list[int] = []
        self.next_id = 50

    async def reply_video(self, **kwargs: Any) -> Any:
        self.next_id += 1
        self.video_ids.append(self.next_id)
        return SimpleNamespace(chat_id=self.chat_id, message_id=self.next_id)

    async def reply_document(self, **kwargs: Any) -> Any:
        self.next_id += 1
        self.document_ids.append(self.next_id)
        return SimpleNamespace(chat_id=self.chat_id, message_id=self.next_id)


def _journal(tmp_path: Path) -> FactoryDeliveryJournal:
    return FactoryDeliveryJournal(
        chat_id=123,
        source_id="video_123",
        source_url="https://youtube.com/watch?v=video_123",
        path=tmp_path / "journal.sqlite3",
    )


@pytest.mark.asyncio
async def test_full_video_repeat_is_suppressed_using_acknowledged_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def requested(_: Any) -> bool:
        return True

    monkeypatch.setattr(full_video, "factory_full_video_requested", requested)
    source = tmp_path / "full.mp4"
    source.write_bytes(b"valid video sample" * 300)
    message = TelegramMessage()
    update = SimpleNamespace(message=message, effective_message=message)
    first = _journal(tmp_path)
    second = _journal(tmp_path)

    assert (
        await full_video.send_factory_full_translation_if_enabled(
            update, source, title="Sermon", duration=123, translation_required=True, delivery_journal=first
        )
        is True
    )
    assert (
        await full_video.send_factory_full_translation_if_enabled(
            update, source, title="Sermon", duration=123, translation_required=True, delivery_journal=second
        )
        is True
    )
    assert message.video_ids == [51]
    assert second.status("full:translated")["message_id"] == 51  # type: ignore[index]


@pytest.mark.asyncio
async def test_editorial_zip_and_followup_files_independently_idempotent(tmp_path: Path) -> None:
    pack = tmp_path / "pack.zip"
    review = tmp_path / "review.json"
    markdown = tmp_path / "review.md"
    pack.write_bytes(b"review-pack")
    review.write_bytes(b'{"review":1}')
    markdown.write_bytes(b"review markdown")
    message = TelegramMessage()
    update = SimpleNamespace(message=message)

    await editorial.send_factory_editorial_files(
        update,
        pack_path=pack,
        review_path=review,
        markdown_path=markdown,
        delivery_journal=_journal(tmp_path),
    )
    await editorial.send_factory_editorial_files(
        update,
        pack_path=pack,
        review_path=review,
        markdown_path=markdown,
        delivery_journal=_journal(tmp_path),
    )
    assert message.document_ids == [51, 52, 53]
    journal = _journal(tmp_path)
    assert journal.status("editorial:pack")["message_id"] == 51  # type: ignore[index]
    assert journal.status("editorial:review")["message_id"] == 52  # type: ignore[index]
    assert journal.status("editorial:markdown")["message_id"] == 53  # type: ignore[index]


@pytest.mark.asyncio
async def test_editorial_review_content_change_blocks_second_send(tmp_path: Path) -> None:
    pack = tmp_path / "pack.zip"
    review = tmp_path / "review.json"
    pack.write_bytes(b"review-pack")
    review.write_bytes(b'{"review":1}')
    message = TelegramMessage()
    update = SimpleNamespace(message=message)

    await editorial.send_factory_editorial_files(
        update, pack_path=pack, review_path=review, delivery_journal=_journal(tmp_path)
    )
    review.write_bytes(b'{"review":2}')
    with pytest.raises(FactoryDeliveryReconciliationRequired, match="fingerprint differs"):
        await editorial.send_factory_editorial_files(
            update, pack_path=pack, review_path=review, delivery_journal=_journal(tmp_path)
        )
    assert message.document_ids == [51, 52]


@pytest.mark.asyncio
async def test_editorial_timeout_requires_operator_and_does_not_resend(tmp_path: Path) -> None:
    class UnconfirmedMessage(TelegramMessage):
        async def reply_document(self, **kwargs: Any) -> Any:
            self.document_ids.append(1)
            raise TimeoutError("response lost after upload")

    pack = tmp_path / "pack.zip"
    pack.write_bytes(b"some bytes")
    message = UnconfirmedMessage()
    update = SimpleNamespace(message=message)

    with pytest.raises(FactoryDeliveryReconciliationRequired):
        await editorial.send_factory_editorial_files(update, pack_path=pack, delivery_journal=_journal(tmp_path))
    with pytest.raises(FactoryDeliveryReconciliationRequired):
        await editorial.send_factory_editorial_files(update, pack_path=pack, delivery_journal=_journal(tmp_path))
    assert message.document_ids == [1]
    assert _journal(tmp_path).status("editorial:pack")["state"] == "sending"  # type: ignore[index]


@pytest.mark.asyncio
async def test_factory_short_renders_but_does_not_resend_confirmed_video(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import pipelines.factory_short_delivery as shorts

    monkeypatch.setattr(shorts, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(shorts, "HAS_FASTER_WHISPER", True)
    monkeypatch.setattr(shorts, "get_shorts_visual_mode", lambda _: "movie")
    monkeypatch.setattr(shorts, "factory_subtitle_profile", lambda: {"model_name": "large-v3", "karaoke": True})
    monkeypatch.setattr(shorts, "get_max_file_size_mb", lambda: 100)
    monkeypatch.setattr(shorts, "file_size_mb", lambda _: 0.05)
    monkeypatch.setattr(shorts, "_FACTORY_CAPTION_BUILDER", lambda **kw: "Caption")
    monkeypatch.setattr(shorts, "media_probe_is_deliverable", lambda _: True)

    async def enriched(candidates: Any, **kwargs: Any) -> Any:
        return candidates

    async def make_video(source: Path, output: Path, *args: Any, **kwargs: Any) -> bool:
        await asyncio.to_thread(output.write_bytes, b"valid fake media")
        return True

    async def transcript(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"start": 0, "end": 2, "text": "Sample"}]

    async def no_thumb(*args: Any, **kwargs: Any) -> bool:
        return False

    async def probe(_: Path) -> Any:
        return SimpleNamespace(duration=60.0)

    monkeypatch.setattr(shorts, "enrich_factory_candidates", enriched)
    monkeypatch.setattr(shorts, "render_short_clip", make_video)
    monkeypatch.setattr(shorts, "postprocess_short", make_video)
    monkeypatch.setattr(shorts, "burn_subtitles_into_short", make_video)
    monkeypatch.setattr(shorts, "transcribe_short_clip", transcript)
    monkeypatch.setattr(shorts, "create_short_title_poster", no_thumb)
    monkeypatch.setattr(shorts, "create_short_snapshot", no_thumb)
    monkeypatch.setattr(shorts, "probe_media_async", probe)
    monkeypatch.setattr(
        shorts,
        "resolve_delivery_timing",
        lambda **kwargs: SimpleNamespace(
            source_start=kwargs["source_start"],
            source_end=kwargs["source_start"] + kwargs["raw_duration"],
            raw_duration=kwargs["raw_duration"],
            delivery_duration=kwargs["final_duration"],
        ),
    )

    original = tmp_path / "original.mp4"
    original.write_bytes(b"source")
    mp3 = tmp_path / "analysis.mp3"
    mp3.write_bytes(b"audio")
    message = TelegramMessage()
    update = SimpleNamespace(message=message)
    candidate = {"start_seconds": 5.0, "end_seconds": 65.0, "title": "Evidence-backed clip"}

    async def run(journal: FactoryDeliveryJournal) -> int:
        return await shorts.process_and_send_factory_shorts(
            url="https://youtube.com/watch?v=video_123",
            media_id="video_123",
            mp3_path=mp3,
            title="Title",
            performer="Author",
            source_duration=120,
            ai_data={},
            candidates=[candidate],
            source_video_path=original,
            update=update,
            delivery_journal=journal,
        )

    assert await run(_journal(tmp_path)) == 1
    assert await run(_journal(tmp_path)) == 1
    assert message.video_ids == [51]
    receipt = _journal(tmp_path).status("short:1")
    assert receipt is not None
    assert receipt["message_id"] == 51
