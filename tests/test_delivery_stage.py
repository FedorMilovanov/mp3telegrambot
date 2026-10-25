#!/usr/bin/env python3
"""Свежая отправка: обязательный MP3 и изолированные необязательные дополнения."""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pipelines.delivery_stage import (
    MAX_PDF_BYTES,
    MIN_PDF_BYTES,
    DeliveryDeps,
    DeliveryRequest,
    deliver_cached_result,
    deliver_result,
    embed_audio_chapters,
    is_retryable_upload,
    production_deps,
    safe_filename,
    send_audio,
    send_full_caption,
    send_pdf,
    send_quiz,
)


class StatusMessage:
    def __init__(self) -> None:
        self.edits: list[str] = []
        self.deleted = False
        self.fail_edit = False
        self.fail_delete = False

    async def edit_text(self, text: str) -> None:
        if self.fail_edit:
            raise RuntimeError("edit failed")
        self.edits.append(text)

    async def delete(self) -> None:
        if self.fail_delete:
            raise RuntimeError("delete failed")
        self.deleted = True


class Chat:
    def __init__(self) -> None:
        self.actions: list[str] = []
        self.fail = False

    async def send_action(self, action: str) -> None:
        if self.fail:
            raise RuntimeError("action failed")
        self.actions.append(action)


class Message:
    def __init__(self) -> None:
        self.chat = Chat()
        self.status = StatusMessage()
        self.replies: list[tuple[str, dict[str, Any]]] = []
        self.documents: list[dict[str, Any]] = []
        self.audio_replies: list[dict[str, Any]] = []
        self.fail_full_caption = False
        self.fail_audio_reply = False

    async def reply_text(self, text: str, **kwargs: Any) -> Any:
        if text == "FULL" and self.fail_full_caption:
            raise RuntimeError("full caption failed")
        self.replies.append((text, kwargs))
        if text == "📄 Генерирую PDF…":
            return self.status
        return SimpleNamespace()

    async def reply_document(self, **kwargs: Any) -> None:
        document = dict(kwargs)
        document["content"] = kwargs["document"].read()
        self.documents.append(document)

    async def reply_audio(self, **kwargs: Any) -> Any:
        if self.fail_audio_reply:
            raise RuntimeError("stale file id")
        self.audio_replies.append(dict(kwargs))
        return SimpleNamespace(kind="cached-audio")


class AudioDelivery:
    def __init__(self, outcomes: list[Any] | None = None, enabled: bool = False) -> None:
        self.outcomes = outcomes or []
        self.enabled = enabled
        self.calls: list[dict[str, Any]] = []
        self.positions: list[int] = []
        self.messages: list[Any] = []

    async def send_or_defer(self, message: Any, **kwargs: Any) -> Any:
        self.messages.append(message)
        self.calls.append(dict(kwargs))
        audio = kwargs["audio"]
        self.positions.append(audio.tell())
        audio.read(1)  # следующая попытка обязана вернуть поток к нулю
        index = len(self.calls) - 1
        outcome = self.outcomes[index] if index < len(self.outcomes) else sent_message()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeDeps:
    def __init__(self) -> None:
        self.settings = {
            "caption_full_text": True,
            "generate_pdf": True,
            "generate_quiz": True,
        }
        self.chapters = [{"time": "0:00", "topic": "Начало"}]
        self.embed_result = True
        self.embed_kwargs: dict[str, Any] = {}
        self.saved_file_ids: list[tuple[str, str]] = []
        self.file_id_attempts: list[tuple[str, str]] = []
        self.pdf_kind = "sent"
        self.pdf_size = 500
        self.pdf_kwargs: dict[str, Any] = {}
        self.questions: list[dict[str, Any]] | None = [{"question": "Q1"}, {"question": "Q2"}]
        self.quiz_kwargs: dict[str, Any] = {}
        self.quiz_send_args: tuple[Any, ...] = ()
        self.quiz_send_kwargs: dict[str, Any] = {}
        self.quiz_sent = 2
        self.errors: dict[str, Exception] = {}
        self.calls: list[tuple[str, Any]] = []

    def deps(self) -> DeliveryDeps:
        return DeliveryDeps(
            get_setting=self.get_setting,
            save_file_id=self.save_file_id,
            parse_chapters=self.parse_chapters,
            embed_chapters=self.embed_chapters,
            generate_pdf=self.generate_pdf,
            generate_quiz=self.generate_quiz,
            send_quiz=self.send_quiz_polls,
            token=self.token,
            sleep=self.sleep,
        )

    def _fail(self, operation: str) -> None:
        if operation in self.errors:
            raise self.errors[operation]

    async def get_setting(self, key: str) -> Any:
        self.calls.append(("setting", key))
        self._fail("get_setting")
        return self.settings.get(key)

    async def save_file_id(self, media_id: str, file_id: str) -> None:
        self.calls.append(("save_file_id", media_id))
        self.file_id_attempts.append((media_id, file_id))
        self._fail("save_file_id" if file_id else "clear_file_id")
        self.saved_file_ids.append((media_id, file_id))

    def parse_chapters(self, raw: Any) -> list[dict[str, Any]]:
        self.calls.append(("parse_chapters", raw))
        self._fail("parse_chapters")
        return self.chapters

    def embed_chapters(self, *args: Any, **kwargs: Any) -> bool:
        self.calls.append(("embed_chapters", args[0]))
        self.embed_kwargs = kwargs
        self._fail("embed_chapters")
        return self.embed_result

    async def generate_pdf(self, **kwargs: Any) -> str | None:
        self.calls.append(("generate_pdf", kwargs["output_path"]))
        self.pdf_kwargs = kwargs
        self._fail("generate_pdf")
        await kwargs["progress_callback"]("Рендер", 50)
        path = Path(kwargs["output_path"])
        if self.pdf_kind == "missing":
            return None
        if self.pdf_kind == "nonexistent":
            return str(path.with_name("missing.pdf"))
        size = {"sent": self.pdf_size, "empty": 100, "large": MAX_PDF_BYTES + 1}[self.pdf_kind]
        with path.open("wb") as output:
            output.truncate(size)
        return str(path)

    async def generate_quiz(self, **kwargs: Any) -> list[dict[str, Any]] | None:
        self.calls.append(("generate_quiz", kwargs["title"]))
        self.quiz_kwargs = kwargs
        self._fail("generate_quiz")
        return self.questions

    async def send_quiz_polls(self, *args: Any, **kwargs: Any) -> int:
        self.calls.append(("send_quiz", args[0]))
        self.quiz_send_args = args
        self.quiz_send_kwargs = kwargs
        self._fail("send_quiz")
        return self.quiz_sent

    def token(self) -> str:
        return "abc123"

    async def sleep(self, delay: float) -> None:
        self.calls.append(("sleep", delay))


def sent_message(file_id: str = "file-id-1") -> Any:
    return SimpleNamespace(audio=SimpleNamespace(file_id=file_id))


def make_request(
    tmp_path: Path,
    message: Message,
    audio: AudioDelivery,
    **overrides: Any,
) -> DeliveryRequest:
    mp3_path = tmp_path / "vid1.mp3"
    mp3_path.write_bytes(b"mp3-data")
    thumbs_dir = tmp_path / "thumbs"
    thumbs_dir.mkdir(exist_ok=True)
    base: dict[str, Any] = {
        "update": SimpleNamespace(message=message),
        "context": SimpleNamespace(name="context"),
        "audio_delivery": audio,
        "mp3_path": mp3_path,
        "media_id": "vid1",
        "source_url": "https://youtu.be/vid1",
        "title": "Исходная тема",
        "performer": "Исходный автор",
        "duration": 125,
        "file_size_mb": 3.5,
        "caption": "CAPTION",
        "full_caption": "FULL",
        "download_dir": tmp_path / "downloads",
        "thumbs_dir": thumbs_dir,
        "ai_data": {
            "real_title": "Тема",
            "real_author": "Автор",
            "timestamps": "0:00 Начало",
        },
        "thumbnail": "thumbnail-buffer",
        "synopsis_url": "https://telegra.ph/synopsis",
        "study_url": "https://telegra.ph/study",
        "reflection_url": "https://telegra.ph/reflection",
        "terms_url": "https://telegra.ph/terms",
        "used_audio_part": "audio-part",
        "used_client": "client",
    }
    base.update(overrides)
    return DeliveryRequest(**base)


class NetworkError(Exception):
    pass


class RetryAfter(Exception):
    def __init__(self, seconds: int) -> None:
        super().__init__(f"retry after {seconds}")
        self.retry_after = seconds


# ── Чистые правила и ID3 ──────────────────────────────────────────────────


def test_pdf_urls_and_safe_filename() -> None:
    safe = DeliveryRequest(
        update=None, context=None, audio_delivery=None, mp3_path=Path("x"),
        media_id="v", source_url="u", title="t", performer="p", duration=1,
        file_size_mb=1, caption="c", full_caption="f", download_dir=Path("d"),
        thumbs_dir=Path("t"), study_url="study", terms_url="terms",
    )
    assert safe.pdf_urls() == {"study": "study", "terms": "terms"}
    assert safe_filename('  a\\/:*?"<>|\n\r\tb  ') == "ab"
    assert safe_filename("\n\t") == "doc"
    assert len(safe_filename("x" * 100)) == 80


@pytest.mark.parametrize(
    "error, expected",
    [
        (NetworkError("offline"), True),
        (TimeoutError("slow"), True),
        (RuntimeError("Bad Gateway"), True),
        (RuntimeError("internal server error"), True),
        (ValueError("Bad Request"), False),
    ],
)
def test_retryable_upload_classification(error: Exception, expected: bool) -> None:
    assert is_retryable_upload(error) is expected


@pytest.mark.asyncio
async def test_chapters_receive_links_metadata_and_thumbnail(tmp_path: Path) -> None:
    fakes = FakeDeps()
    request = make_request(tmp_path, Message(), AudioDelivery())
    thumbnail = request.thumbs_dir / "vid1_thumb.jpg"
    thumbnail.write_bytes(b"jpg")

    embedded = await embed_audio_chapters(request, fakes.deps())

    assert embedded is True
    assert fakes.embed_kwargs == {
        "title": "Тема",
        "performer": "Автор",
        "thumb_path": thumbnail,
        "comment": (
            "Source: https://youtu.be/vid1\n"
            "Synopsis: https://telegra.ph/synopsis\n"
            "Study: https://telegra.ph/study\n"
            "Reflection: https://telegra.ph/reflection"
        ),
    }


@pytest.mark.asyncio
async def test_chapters_empty_and_failure_are_nonfatal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    empty = FakeDeps()
    empty.chapters = []
    request = make_request(
        tmp_path,
        Message(),
        AudioDelivery(),
        ai_data=None,
        synopsis_url="",
        study_url="",
        reflection_url="",
    )
    assert await embed_audio_chapters(request, empty.deps()) is False
    assert not any(name == "embed_chapters" for name, _ in empty.calls)

    broken = FakeDeps()
    broken.errors["parse_chapters"] = RuntimeError("bad timestamps")
    with caplog.at_level(logging.WARNING):
        assert await embed_audio_chapters(request, broken.deps()) is False
    assert "MP3 chapters skip: bad timestamps" in caplog.text


# ── Обязательный MP3 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audio_success_exact_kwargs_and_file_id(tmp_path: Path) -> None:
    message = Message()
    audio = AudioDelivery([sent_message("fresh-fid")])
    fakes = FakeDeps()
    request = make_request(tmp_path, message, audio)

    result = await send_audio(request, fakes.deps())

    assert result.audio.file_id == "fresh-fid"
    assert message.chat.actions == ["upload_voice"]
    assert fakes.saved_file_ids == [("vid1", "fresh-fid")]
    assert audio.positions == [0]
    kwargs = audio.calls[0]
    assert kwargs["fallback_path"] == request.mp3_path
    assert kwargs["title"] == "Тема"
    assert kwargs["performer"] == "Автор"
    assert kwargs["thumbnail"] == "thumbnail-buffer"
    assert kwargs["duration"] == 125
    assert kwargs["caption"] == "CAPTION"
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["write_timeout"] == 180
    assert kwargs["read_timeout"] == 180
    assert kwargs["connect_timeout"] == 60


@pytest.mark.asyncio
async def test_audio_deferral_without_message_is_success(tmp_path: Path) -> None:
    audio = AudioDelivery([None])
    fakes = FakeDeps()

    result = await send_audio(make_request(tmp_path, Message(), audio), fakes.deps())

    assert result is None
    assert len(audio.calls) == 1
    assert fakes.saved_file_ids == []


@pytest.mark.asyncio
async def test_audio_network_retries_reset_stream_and_backoff(tmp_path: Path) -> None:
    audio = AudioDelivery([NetworkError("one"), NetworkError("two"), sent_message("")])
    fakes = FakeDeps()
    request = make_request(tmp_path, Message(), audio, announce_upload=False)

    await send_audio(request, fakes.deps())

    assert len(audio.calls) == 3
    assert audio.positions == [0, 0, 0]
    assert [value for name, value in fakes.calls if name == "sleep"] == [5, 10]
    assert fakes.saved_file_ids == []


@pytest.mark.asyncio
async def test_audio_retry_after_waits_api_delay(tmp_path: Path) -> None:
    audio = AudioDelivery([RetryAfter(2), sent_message()])
    fakes = FakeDeps()

    await send_audio(make_request(tmp_path, Message(), audio), fakes.deps())

    assert [value for name, value in fakes.calls if name == "sleep"] == [3.0]


@pytest.mark.asyncio
async def test_audio_exhausts_retries_and_raises(tmp_path: Path) -> None:
    audio = AudioDelivery([NetworkError("1"), NetworkError("2"), NetworkError("3")])
    fakes = FakeDeps()

    with pytest.raises(NetworkError, match="3"):
        await send_audio(make_request(tmp_path, Message(), audio), fakes.deps())

    assert len(audio.calls) == 3
    assert [value for name, value in fakes.calls if name == "sleep"] == [5, 10]


@pytest.mark.asyncio
async def test_audio_non_retryable_raises_and_action_failure_is_ignored(tmp_path: Path) -> None:
    message = Message()
    message.chat.fail = True
    audio = AudioDelivery([ValueError("bad request")])
    fakes = FakeDeps()

    with pytest.raises(ValueError, match="bad request"):
        await send_audio(make_request(tmp_path, message, audio), fakes.deps())

    assert len(audio.calls) == 1
    assert not any(name == "sleep" for name, _ in fakes.calls)


@pytest.mark.asyncio
async def test_audio_file_id_cache_failure_does_not_cancel_delivery(tmp_path: Path) -> None:
    fakes = FakeDeps()
    fakes.errors["save_file_id"] = RuntimeError("database busy")

    result = await send_audio(
        make_request(tmp_path, Message(), AudioDelivery()), fakes.deps()
    )

    assert result.audio.file_id == "file-id-1"


# ── Полный текст ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "enabled, full, caption, expected",
    [
        (False, "FULL", "CAPTION", False),
        (True, "", "CAPTION", False),
        (True, "SAME", "SAME", False),
        (True, "FULL", "CAPTION", True),
    ],
)
async def test_full_caption_send_conditions(
    tmp_path: Path, enabled: bool, full: str, caption: str, expected: bool
) -> None:
    message = Message()
    fakes = FakeDeps()
    fakes.settings["caption_full_text"] = enabled
    request = make_request(tmp_path, message, AudioDelivery(), full_caption=full, caption=caption)

    assert await send_full_caption(request, fakes.deps()) is expected
    assert bool(message.replies) is expected


@pytest.mark.asyncio
async def test_full_caption_failure_is_nonfatal(tmp_path: Path) -> None:
    message = Message()
    message.fail_full_caption = True
    fakes = FakeDeps()

    assert await send_full_caption(
        make_request(tmp_path, message, AudioDelivery()), fakes.deps()
    ) is False


# ── PDF ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["disabled", "no_analysis", "no_urls"])
async def test_pdf_skips_without_work(tmp_path: Path, mode: str) -> None:
    fakes = FakeDeps()
    overrides: dict[str, Any] = {}
    if mode == "disabled":
        fakes.settings["generate_pdf"] = False
    elif mode == "no_analysis":
        overrides["ai_data"] = None
    else:
        overrides.update(synopsis_url="", study_url="", reflection_url="", terms_url="")
    request = make_request(tmp_path, Message(), AudioDelivery(), **overrides)

    outcome = await send_pdf(request, fakes.deps())

    assert outcome == mode
    assert not any(name == "generate_pdf" for name, _ in fakes.calls)


@pytest.mark.asyncio
async def test_pdf_success_progress_document_and_cleanup(tmp_path: Path) -> None:
    message = Message()
    fakes = FakeDeps()
    request = make_request(tmp_path, message, AudioDelivery())

    outcome = await send_pdf(request, fakes.deps())

    assert outcome == "sent"
    assert fakes.pdf_kwargs["title"] == "Тема"
    assert fakes.pdf_kwargs["performer"] == "Автор"
    assert fakes.pdf_kwargs["duration_str"] == "2:05"
    assert fakes.pdf_kwargs["urls"] == request.pdf_urls()
    assert message.status.edits == ["📄 PDF: Рендер (50%)"]
    assert message.status.deleted is True
    assert len(message.documents) == 1
    document = message.documents[0]
    assert document["filename"] == "Автор — Тема.pdf"
    assert document["caption"] == "📄 <b>PDF-версия материала</b>"
    assert document["parse_mode"] == "HTML"
    assert len(document["content"]) == 500
    assert not Path(fakes.pdf_kwargs["output_path"]).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [MIN_PDF_BYTES, MAX_PDF_BYTES])
async def test_pdf_size_boundaries_are_inclusive(tmp_path: Path, size: int) -> None:
    message = Message()
    fakes = FakeDeps()
    fakes.pdf_size = size

    assert await send_pdf(make_request(tmp_path, message, AudioDelivery()), fakes.deps()) == "sent"
    assert len(message.documents) == 1
    assert message.status.deleted is True


@pytest.mark.asyncio
async def test_pdf_empty_file_gets_specific_error(tmp_path: Path) -> None:
    message = Message()
    fakes = FakeDeps()
    fakes.pdf_kind = "empty"

    assert await send_pdf(make_request(tmp_path, message, AudioDelivery()), fakes.deps()) == "empty"

    assert message.documents == []
    assert message.replies[-1][0] == "❌ Не удалось создать PDF (файл пустой)."
    assert message.status.deleted is True


@pytest.mark.asyncio
async def test_pdf_large_file_gets_specific_error(tmp_path: Path) -> None:
    message = Message()
    fakes = FakeDeps()
    fakes.pdf_kind = "large"

    assert await send_pdf(make_request(tmp_path, message, AudioDelivery()), fakes.deps()) == "too_large"

    assert message.documents == []
    assert message.replies[-1][0] == "❌ PDF слишком большой для отправки."


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "nonexistent"])
async def test_pdf_missing_result_gets_error(tmp_path: Path, kind: str) -> None:
    message = Message()
    fakes = FakeDeps()
    fakes.pdf_kind = kind

    assert await send_pdf(make_request(tmp_path, message, AudioDelivery()), fakes.deps()) == "missing"

    assert message.documents == []
    assert message.replies[-1][0] == "❌ Не удалось создать PDF."


@pytest.mark.asyncio
async def test_pdf_generation_failure_is_nonfatal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    message = Message()
    fakes = FakeDeps()
    fakes.errors["generate_pdf"] = RuntimeError("renderer failed")

    with caplog.at_level(logging.WARNING):
        outcome = await send_pdf(make_request(tmp_path, message, AudioDelivery()), fakes.deps())

    assert outcome == "error"
    assert message.status.deleted is True
    assert "PDF генерация не удалась: renderer failed" in caplog.text


@pytest.mark.asyncio
async def test_pdf_empty_metadata_uses_placeholders(tmp_path: Path) -> None:
    fakes = FakeDeps()
    request = make_request(
        tmp_path,
        Message(),
        AudioDelivery(),
        ai_data={"real_title": " ", "real_author": " "},
        title="",
        performer="",
        duration=0,
    )

    assert await send_pdf(request, fakes.deps()) == "sent"
    assert fakes.pdf_kwargs["title"] == "Без названия"
    assert fakes.pdf_kwargs["performer"] == "Неизвестный"
    assert fakes.pdf_kwargs["duration_str"] == ""


@pytest.mark.asyncio
async def test_pdf_progress_and_cleanup_failures_are_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = Message()
    message.status.fail_edit = True
    message.status.fail_delete = True
    fakes = FakeDeps()

    def fail_unlink(*args: Any, **kwargs: Any) -> None:
        raise OSError("locked")

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    assert await send_pdf(make_request(tmp_path, message, AudioDelivery()), fakes.deps()) == "sent"
    assert len(message.documents) == 1


@pytest.mark.asyncio
async def test_pdf_mkdir_failure_has_no_status_or_leaked_exception(tmp_path: Path) -> None:
    fakes = FakeDeps()
    download_file = tmp_path / "not-a-directory"
    download_file.write_text("x", encoding="utf-8")

    outcome = await send_pdf(
        make_request(tmp_path, Message(), AudioDelivery(), download_dir=download_file),
        fakes.deps(),
    )

    assert outcome == "error"


# ── Quiz ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["disabled", "no_analysis"])
async def test_quiz_skips_without_generation(tmp_path: Path, mode: str) -> None:
    fakes = FakeDeps()
    overrides: dict[str, Any] = {}
    if mode == "disabled":
        fakes.settings["generate_quiz"] = False
    else:
        overrides["ai_data"] = None

    assert await send_quiz(
        make_request(tmp_path, Message(), AudioDelivery(), **overrides), fakes.deps()
    ) == 0
    assert not any(name == "generate_quiz" for name, _ in fakes.calls)


@pytest.mark.asyncio
async def test_quiz_header_is_escaped_and_audio_context_reused(tmp_path: Path) -> None:
    message = Message()
    fakes = FakeDeps()
    request = make_request(
        tmp_path,
        message,
        AudioDelivery(),
        ai_data={"real_title": "A&B <Тема>", "real_author": "Автор"},
    )

    sent = await send_quiz(request, fakes.deps())

    assert sent == 2
    assert fakes.quiz_kwargs["existing_audio_part"] == "audio-part"
    assert fakes.quiz_kwargs["existing_client"] == "client"
    assert message.replies == [
        (
            "🧠 <b>Quiz: A&amp;B &lt;Тема&gt;</b>\n"
            "Проверь понимание материала — 2 вопросов:",
            {"parse_mode": "HTML"},
        )
    ]
    assert fakes.quiz_send_args[0] == fakes.questions
    assert fakes.quiz_send_args[1] is request.update
    assert fakes.quiz_send_args[2] is request.context
    assert fakes.quiz_send_kwargs == {"title": "A&B <Тема>"}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["empty", "error"])
async def test_quiz_empty_and_failure_are_nonfatal(tmp_path: Path, mode: str) -> None:
    fakes = FakeDeps()
    if mode == "empty":
        fakes.questions = None
    else:
        fakes.errors["generate_quiz"] = RuntimeError("Gemini unavailable")

    assert await send_quiz(make_request(tmp_path, Message(), AudioDelivery()), fakes.deps()) == 0
    assert not any(name == "send_quiz" for name, _ in fakes.calls)


# ── Cache-hit: instant resend и исторические отличия ─────────────────────


@pytest.mark.asyncio
async def test_cached_chapters_keep_legacy_comment_and_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    fakes = FakeDeps()
    request = make_request(tmp_path, Message(), AudioDelivery(), cache_hit=True)

    assert await embed_audio_chapters(request, fakes.deps()) is True
    assert fakes.embed_kwargs["comment"] == (
        "Source: https://youtu.be/vid1\nSynopsis: https://telegra.ph/synopsis"
    )

    broken = FakeDeps()
    broken.errors["parse_chapters"] = RuntimeError("bad cached timestamps")
    with caplog.at_level(logging.WARNING):
        assert await embed_audio_chapters(request, broken.deps()) is False
    assert "MP3 chapters (кэш) skip: bad cached timestamps" in caplog.text


@pytest.mark.asyncio
async def test_cached_instant_resend_skips_upload_chapters_and_quiz(tmp_path: Path) -> None:
    message = Message()
    audio = AudioDelivery()
    fakes = FakeDeps()
    request = make_request(tmp_path, message, audio, cached_file_id="stored-fid")

    outcome = await deliver_cached_result(request, fakes.deps())

    assert outcome.audio_via_file_id is True
    assert outcome.audio_message.kind == "cached-audio"
    assert outcome.chapters_embedded is False
    assert outcome.full_caption_sent is True
    assert outcome.pdf_status == "sent"
    assert outcome.quiz_sent == 0
    assert message.audio_replies == [
        {"audio": "stored-fid", "caption": "CAPTION", "parse_mode": "HTML"}
    ]
    assert audio.calls == []
    assert fakes.saved_file_ids == []
    assert not any(name == "parse_chapters" for name, _ in fakes.calls)
    assert not any(name == "generate_quiz" for name, _ in fakes.calls)
    assert len(message.documents) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("clear_fails", [False, True])
async def test_cached_stale_id_clears_and_falls_back(
    tmp_path: Path, clear_fails: bool
) -> None:
    message = Message()
    message.fail_audio_reply = True
    audio = AudioDelivery([sent_message("replacement-fid")])
    fakes = FakeDeps()
    fakes.settings["caption_full_text"] = False
    fakes.settings["generate_pdf"] = False
    if clear_fails:
        fakes.errors["clear_file_id"] = RuntimeError("database read only")
    request = make_request(tmp_path, message, audio, cached_file_id="stale-fid")

    outcome = await deliver_cached_result(request, fakes.deps())

    assert outcome.audio_via_file_id is False
    assert outcome.chapters_embedded is True
    assert outcome.audio_message.audio.file_id == "replacement-fid"
    assert len(audio.calls) == 1
    assert fakes.file_id_attempts == [("vid1", ""), ("vid1", "replacement-fid")]
    expected_saved = [] if clear_fails else [("vid1", "")]
    assert fakes.saved_file_ids == expected_saved + [("vid1", "replacement-fid")]
    assert not any(name == "generate_quiz" for name, _ in fakes.calls)


@pytest.mark.asyncio
async def test_cached_deferral_bypasses_instant_id(tmp_path: Path) -> None:
    message = Message()
    audio = AudioDelivery([None], enabled=True)
    fakes = FakeDeps()
    fakes.settings["caption_full_text"] = False
    fakes.settings["generate_pdf"] = False
    request = make_request(tmp_path, message, audio, cached_file_id="stored-fid")

    outcome = await deliver_cached_result(request, fakes.deps())

    assert outcome.audio_via_file_id is False
    assert outcome.audio_message is None
    assert message.audio_replies == []
    assert len(audio.calls) == 1
    assert fakes.file_id_attempts == []


@pytest.mark.asyncio
async def test_cached_without_id_retries_with_legacy_log_and_no_action(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    message = Message()
    audio = AudioDelivery([NetworkError("offline"), sent_message("new-fid")])
    fakes = FakeDeps()
    fakes.settings["caption_full_text"] = False
    fakes.settings["generate_pdf"] = False
    request = make_request(tmp_path, message, audio)

    with caplog.at_level(logging.WARNING):
        outcome = await deliver_cached_result(request, fakes.deps())

    assert outcome.audio_message.audio.file_id == "new-fid"
    assert message.chat.actions == []
    assert [value for name, value in fakes.calls if name == "sleep"] == [5]
    assert "Кэш upload попытка 1/3 (NetworkError: offline), повтор..." in caplog.text
    assert "Кэш upload попытка 1/3 не удалась" not in caplog.text


@pytest.mark.asyncio
async def test_cached_full_caption_failure_keeps_legacy_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    message = Message()
    message.fail_full_caption = True
    request = make_request(tmp_path, message, AudioDelivery(), cache_hit=True)

    with caplog.at_level(logging.WARNING):
        assert await send_full_caption(request, FakeDeps().deps()) is False
    assert "Кэш full caption send error: full caption failed" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [MIN_PDF_BYTES, MAX_PDF_BYTES])
async def test_cached_pdf_boundaries_remain_strict(tmp_path: Path, size: int) -> None:
    message = Message()
    fakes = FakeDeps()
    fakes.pdf_size = size
    request = make_request(tmp_path, message, AudioDelivery(), cache_hit=True)

    assert await send_pdf(request, fakes.deps()) == "invalid_size"
    assert message.documents == []
    assert message.replies[-1][0] == "❌ Не удалось создать PDF."
    assert message.status.deleted is True


@pytest.mark.asyncio
async def test_cached_pdf_import_error_is_silent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    message = Message()
    fakes = FakeDeps()
    fakes.errors["generate_pdf"] = ImportError("reportlab unavailable")
    request = make_request(tmp_path, message, AudioDelivery(), cache_hit=True)

    with caplog.at_level(logging.WARNING):
        assert await send_pdf(request, fakes.deps()) == "unavailable"
    assert "PDF (кэш) не удался" not in caplog.text
    assert message.status.deleted is True


@pytest.mark.asyncio
async def test_cached_pdf_other_error_keeps_legacy_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    message = Message()
    fakes = FakeDeps()
    fakes.errors["generate_pdf"] = RuntimeError("cached renderer failed")
    request = make_request(tmp_path, message, AudioDelivery(), cache_hit=True)

    with caplog.at_level(logging.WARNING):
        assert await send_pdf(request, fakes.deps()) == "error"
    assert "PDF (кэш) не удался: cached renderer failed" in caplog.text


# ── Оркестратор и боевые границы ──────────────────────────────────────────


def test_main_pipeline_wires_delivery_stages() -> None:
    source = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    cache_stage = Path("pipelines/cache_hit_delivery_stage.py").read_text(encoding="utf-8")
    assert "from pipelines.delivery_stage import DeliveryRequest, deliver_cached_result, deliver_result" in source
    assert "await deliver_result(" in source
    assert "deliver=deliver_cached_result" in source
    assert "await deliver(" in cache_stage
    assert "audio_delivery=_source_audio_delivery" in source
    assert "used_audio_part=used_audio_part" in source
    assert 'cached_file_id=(cached or {}).get("audio_file_id")' in source


@pytest.mark.asyncio
async def test_deliver_result_order_and_structured_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipelines import delivery_stage as stage

    fakes = FakeDeps()
    deps = fakes.deps()
    request = make_request(tmp_path, Message(), AudioDelivery())
    events: list[str] = []
    marker = object()

    async def chapters(req: DeliveryRequest, got: DeliveryDeps) -> bool:
        events.append("chapters")
        return True

    async def audio(req: DeliveryRequest, got: DeliveryDeps) -> Any:
        events.append("audio")
        return marker

    async def full(req: DeliveryRequest, got: DeliveryDeps) -> bool:
        events.append("full")
        return True

    async def pdf(req: DeliveryRequest, got: DeliveryDeps) -> str:
        events.append("pdf")
        return "sent"

    async def quiz(req: DeliveryRequest, got: DeliveryDeps) -> int:
        events.append("quiz")
        return 2

    monkeypatch.setattr(stage, "production_deps", lambda: deps)
    monkeypatch.setattr(stage, "embed_audio_chapters", chapters)
    monkeypatch.setattr(stage, "send_audio", audio)
    monkeypatch.setattr(stage, "send_full_caption", full)
    monkeypatch.setattr(stage, "send_pdf", pdf)
    monkeypatch.setattr(stage, "send_quiz", quiz)

    outcome = await deliver_result(request)

    assert events == ["chapters", "audio", "full", "pdf", "quiz"]
    assert outcome.chapters_embedded is True
    assert outcome.audio_message is marker
    assert outcome.full_caption_sent is True
    assert outcome.pdf_status == "sent"
    assert outcome.quiz_sent == 2


@pytest.mark.asyncio
async def test_audio_failure_stops_optional_extras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipelines import delivery_stage as stage

    request = make_request(tmp_path, Message(), AudioDelivery())
    deps = FakeDeps().deps()

    async def chapters(req: DeliveryRequest, got: DeliveryDeps) -> bool:
        return False

    async def audio(req: DeliveryRequest, got: DeliveryDeps) -> Any:
        raise NetworkError("upload failed")

    async def forbidden(req: DeliveryRequest, got: DeliveryDeps) -> Any:
        raise AssertionError("optional stage must not run")

    monkeypatch.setattr(stage, "embed_audio_chapters", chapters)
    monkeypatch.setattr(stage, "send_audio", audio)
    monkeypatch.setattr(stage, "send_full_caption", forbidden)
    monkeypatch.setattr(stage, "send_pdf", forbidden)
    monkeypatch.setattr(stage, "send_quiz", forbidden)

    with pytest.raises(NetworkError, match="upload failed"):
        await deliver_result(request, deps)


def test_production_deps_are_real_lightweight_boundaries() -> None:
    from core.database import adb_set_audio_file_id, asettings_get
    from services.mp3_chapters import embed_chapters, timestamps_to_chapter_list

    deps = production_deps()

    assert deps.get_setting is asettings_get
    assert deps.save_file_id is adb_set_audio_file_id
    assert deps.parse_chapters is timestamps_to_chapter_list
    assert deps.embed_chapters is embed_chapters
    assert len(deps.token()) == 6


@pytest.mark.asyncio
async def test_lazy_pdf_and_quiz_wrappers_call_current_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipelines import delivery_stage as stage
    import services.pdf_generator as pdf_service
    import services.quiz_generator as quiz_service

    async def pdf(*args: Any, **kwargs: Any) -> str:
        return "pdf-result"

    async def quiz(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"question": "Q"}]

    async def send(*args: Any, **kwargs: Any) -> int:
        return 7

    monkeypatch.setattr(pdf_service, "generate_sermon_pdf_async", pdf)
    monkeypatch.setattr(quiz_service, "generate_quiz", quiz)
    monkeypatch.setattr(quiz_service, "send_quiz_polls", send)

    assert await stage._generate_pdf("arg", key="value") == "pdf-result"
    assert await stage._generate_quiz("arg") == [{"question": "Q"}]
    assert await stage._send_quiz("arg") == 7
