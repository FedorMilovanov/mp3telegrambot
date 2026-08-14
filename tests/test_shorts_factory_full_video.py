from types import SimpleNamespace

import pytest

import handlers.mode_command as mode_command
import services.shorts_factory_full_video as full_video


def _button_texts(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def test_factory_full_video_toggle_ui():
    regular = _button_texts(mode_command._analysis_keyboard("rus"))
    factory_off = _button_texts(mode_command._analysis_keyboard("shorts_max", full_video=False))
    factory_on = _button_texts(mode_command._analysis_keyboard("shorts_max", full_video=True))

    assert not any("полного видео" in text for text in regular)
    assert "⬜ Только нарезки (без полного видео)" in factory_off
    assert "☑️ Полный видео-перевод + нарезки" in factory_on


def test_factory_full_video_text_documents_no_retranslation():
    text = mode_command._analysis_text("shorts_max", full_video=True)
    assert "тот же готовый Yandex LiveDub-файл" in text
    assert "повторный перевод не запускается" in text


@pytest.mark.asyncio
async def test_full_translation_delivery_reuses_existing_file(monkeypatch, tmp_path):
    async def enabled(_update):
        return True

    monkeypatch.setattr(full_video, "factory_full_video_requested", enabled)
    path = tmp_path / "sermon_factory_source.mp4"
    path.write_bytes(b"x" * 2048)

    class Message:
        def __init__(self):
            self.calls = []

        async def reply_video(self, **kwargs):
            self.calls.append(kwargs)
            return "sent"

        async def reply_text(self, _text):
            return None

    message = Message()
    update = SimpleNamespace(effective_message=message, message=message)
    sent = await full_video.send_factory_full_translation_if_enabled(
        update,
        path,
        title="Полная Проповедь",
        duration=3600,
        translation_required=True,
    )

    assert sent is True
    assert len(message.calls) == 1
    assert message.calls[0]["duration"] == 3600
    assert message.calls[0]["supports_streaming"] is True


@pytest.mark.asyncio
async def test_full_translation_not_sent_when_source_needs_no_translation(monkeypatch, tmp_path):
    async def enabled(_update):
        return True

    monkeypatch.setattr(full_video, "factory_full_video_requested", enabled)
    path = tmp_path / "sermon_factory_source.mp4"
    path.write_bytes(b"x" * 2048)

    class Message:
        async def reply_video(self, **_kwargs):
            raise AssertionError("unexpected send")

    message = Message()
    update = SimpleNamespace(effective_message=message, message=message)
    sent = await full_video.send_factory_full_translation_if_enabled(
        update,
        path,
        title="Проповедь",
        duration=1200,
        translation_required=False,
    )

    assert sent is False
