"""Executed project, text, and document IO contracts for Dub Studio wizard."""

import asyncio
from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop

from handlers import dub_wizard as wizard


@pytest.fixture(autouse=True)
def _admin_ids(monkeypatch):
    monkeypatch.setattr(wizard, "ADMIN_IDS", {1})


class Message:
    def __init__(self, *, text="", document=None):
        self.text = text
        self.document = document
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


def _update(*, user_id=1, with_user=True, with_message=True, text="", document=None):
    message = Message(text=text, document=document) if with_message else None
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id) if with_user else None,
        effective_message=message,
        effective_chat=SimpleNamespace(id=777),
    )
    return update, message


def _context(*, args=None, state=None, bot=None):
    return SimpleNamespace(
        args=args,
        user_data={} if state is None else state,
        bot=bot,
    )


def _run(callback, *args):
    return asyncio.run(callback(*args))


def _choice():
    return SimpleNamespace(
        profile_id="profile",
        display_name="<Profile>",
        backend_id="voxcpm2",
        model_revision="revision",
        fingerprint="f" * 64,
    )


def test_direct_project_creation_persists_request_and_waits_for_srt(monkeypatch, tmp_path):
    choice = _choice()

    class Store:
        def __init__(self):
            self.created = []

        def create_project(self, *args, **kwargs):
            self.created.append((args, kwargs))
            return {"id": "project"}

        def enqueue_job(self, *_args):
            raise AssertionError("direct projects wait for SRT before enqueue")

    store = Store()
    writes = []
    monkeypatch.setattr(wizard, "DubStore", lambda: store)
    monkeypatch.setattr(
        wizard,
        "_extract_youtube_video_id",
        lambda _url: ("VideoId123", "https://youtube.com/watch?v=VideoId123"),
    )
    monkeypatch.setattr(
        wizard,
        "_request_payload",
        lambda *_args: {"speech_model_profile": "profile"},
    )
    monkeypatch.setattr(wizard, "production_tts_profile_choice", lambda _value: choice)
    monkeypatch.setattr(wizard, "_project_root", lambda _project_id: tmp_path)
    monkeypatch.setattr(
        wizard.generic_project_runtime,
        "validate_request_payload",
        lambda request: {**request, "validated": True},
    )
    monkeypatch.setattr(
        wizard,
        "write_durable_request",
        lambda path, request: writes.append((path, request)),
    )
    update, message = _update()
    context = _context(state={wizard._WIZARD_KEY: {"awaiting": "url"}})

    _run(
        wizard._create_generic_project,
        update,
        context,
        "https://youtu.be/VideoId123",
        "direct",
        "profile",
    )

    args, kwargs = store.created[0]
    assert args == ("generic_short_v1",)
    assert kwargs["title"] == "Видео VideoId123 — мой готовый SRT"
    assert kwargs["metadata"]["translation_mode"] == "direct"
    assert kwargs["metadata"]["speech_profile_fingerprint"] == "f" * 64
    assert writes == [(tmp_path / "request.json", {"speech_model_profile": "profile", "validated": True})]
    assert context.user_data[wizard._WIZARD_KEY] == {
        "awaiting": "srt",
        "mode": "direct",
        "project_id": "project",
    }
    text, reply_kwargs = message.replies[0]
    assert "Ссылка принята" in text
    assert "&lt;Profile&gt;" in text
    callbacks = [button.callback_data for row in reply_kwargs["reply_markup"].inline_keyboard for button in row]
    assert "dubwiz|srt|project" in callbacks


def test_decode_text_file_rejects_empty_and_non_srt_payloads():
    with pytest.raises(RuntimeError, match="Файл пуст"):
        wizard._decode_text_file(b"")
    with pytest.raises(RuntimeError, match="Не удалось прочитать SRT"):
        wizard._decode_text_file(b"not an SRT payload")


def test_store_ready_srt_validates_state_owner_and_mode(monkeypatch):
    update, _ = _update()
    with pytest.raises(RuntimeError, match="Сначала выберите режим"):
        _run(wizard._store_ready_srt, update, _context(), "text")

    project = {
        "id": "p",
        "owner_user_id": 2,
        "metadata": {"translation_mode": "direct"},
    }

    class Store:
        def get_project(self, _project_id):
            return project

    monkeypatch.setattr(wizard, "DubStore", Store)
    context = _context(state={wizard._WIZARD_KEY: {"awaiting": "srt", "project_id": "p"}})
    with pytest.raises(PermissionError, match="не ваш проект"):
        _run(wizard._store_ready_srt, update, context, "text")
    project.update(owner_user_id=1, metadata={"translation_mode": "gemini"})
    with pytest.raises(RuntimeError, match="не находится в режиме"):
        _run(wizard._store_ready_srt, update, context, "text")


def test_store_ready_srt_normalizes_file_enqueues_and_clears_state(monkeypatch, tmp_path):
    project = {
        "id": "p",
        "owner_user_id": 1,
        "metadata": {"translation_mode": "direct"},
    }

    class Store:
        def __init__(self):
            self.jobs = []

        def get_project(self, _project_id):
            return project

        def enqueue_job(self, project_id, action):
            self.jobs.append((project_id, action))
            return {"id": 91}

    store = Store()
    monkeypatch.setattr(wizard, "DubStore", lambda: store)
    monkeypatch.setattr(wizard, "_project_root", lambda _project_id: tmp_path)
    monkeypatch.setattr(wizard, "parse_srt_text", lambda _text: [object(), object()])
    update, message = _update()
    context = _context(state={wizard._WIZARD_KEY: {"awaiting": "srt", "project_id": "p"}})
    source = "\ufeff1\r\n00:00:00,000 --> 00:00:01,000\r\nПривет\r\n"

    _run(wizard._store_ready_srt, update, context, source)

    assert (tmp_path / "input" / "ready_translation.srt").read_text(
        encoding="utf-8"
    ) == "1\n00:00:00,000 --> 00:00:01,000\nПривет\n"
    assert store.jobs == [("p", "render_direct")]
    assert context.user_data == {}
    text, kwargs = message.replies[0]
    assert "Реплик: <b>2</b>" in text
    assert "задание <b>#91</b>" in text
    assert kwargs == {"parse_mode": "HTML"}


def test_dubsrt_supports_explicit_latest_and_missing_projects(monkeypatch):
    project = {"id": "p"}
    latest = {"project": project}

    class Store:
        def get_project(self, token):
            assert token == "p"
            return project

    monkeypatch.setattr(wizard, "DubStore", Store)
    monkeypatch.setattr(wizard, "_latest_direct_draft", lambda _user_id: latest["project"])
    activated = []

    async def activate(_update, _context, project_id):
        activated.append(project_id)

    monkeypatch.setattr(wizard, "_activate_srt_upload", activate)
    update, message = _update()

    _run(wizard.dubsrt_command, update, _context(args=[" P "]))
    _run(wizard.dubsrt_command, update, _context(args=[]))
    assert activated == ["p", "p"]

    latest["project"] = None
    _run(wizard.dubsrt_command, update, _context(args=[]))
    assert "Не найден ожидающий SRT-проект" in message.replies[0][0]


def test_translation_alias_delegates_to_dubsrt(monkeypatch):
    calls = []

    async def dubsrt(update, context):
        calls.append((update, context))

    monkeypatch.setattr(wizard, "dubsrt_command", dubsrt)
    update, _ = _update()
    context = _context()
    _run(wizard.dubtranslation_command, update, context)
    assert calls == [(update, context)]


def test_text_handler_guards_and_profile_prompt(monkeypatch):
    update, _ = _update(with_user=False)
    _run(wizard.handle_dub_wizard_text, update, _context(state=None))
    update, _ = _update()
    _run(wizard.handle_dub_wizard_text, update, _context(state=None))

    update, message = _update(user_id=2, text="hello")
    context = _context(state={wizard._WIZARD_KEY: {"awaiting": "url"}})
    with pytest.raises(ApplicationHandlerStop):
        _run(wizard.handle_dub_wizard_text, update, context)
    assert message.replies[0][0].startswith("⛔")

    update, message = _update(text="hello")
    context = _context(state={wizard._WIZARD_KEY: {"awaiting": "tts_profile"}})
    with pytest.raises(ApplicationHandlerStop):
        _run(wizard.handle_dub_wizard_text, update, context)
    assert "выберите TTS-модель" in message.replies[0][0]

    context.user_data[wizard._WIZARD_KEY] = {"awaiting": "unknown"}
    _run(wizard.handle_dub_wizard_text, update, context)


def test_text_handler_url_success_and_validation_error(monkeypatch):
    calls = []

    async def create(_update, _context, text, mode, profile_id):
        calls.append((text, mode, profile_id))

    monkeypatch.setattr(wizard, "_create_generic_project", create)
    update, _message = _update(text=" https://youtu.be/VideoId123 ")
    context = _context(
        state={
            wizard._WIZARD_KEY: {
                "awaiting": "url",
                "mode": "direct",
                "profile_id": "profile",
            }
        }
    )
    with pytest.raises(ApplicationHandlerStop):
        _run(wizard.handle_dub_wizard_text, update, context)
    assert calls == [("https://youtu.be/VideoId123", "direct", "profile")]

    update, message = _update(text="https://youtu.be/VideoId123")
    context = _context(state={wizard._WIZARD_KEY: {"awaiting": "url", "mode": "direct"}})
    with pytest.raises(ApplicationHandlerStop):
        _run(wizard.handle_dub_wizard_text, update, context)
    assert "TTS profile не выбран" in message.replies[0][0]
    assert "Пришлите корректную ссылку" in message.replies[0][0]


def test_text_handler_srt_success_and_error(monkeypatch):
    stored = []

    async def store(_update, _context, text):
        stored.append(text)

    monkeypatch.setattr(wizard, "_store_ready_srt", store)
    update, _message = _update(text=" ready srt ")
    context = _context(state={wizard._WIZARD_KEY: {"awaiting": "srt", "project_id": "p"}})
    with pytest.raises(ApplicationHandlerStop):
        _run(wizard.handle_dub_wizard_text, update, context)
    assert stored == ["ready srt"]

    async def fail(_update, _context, _text):
        raise RuntimeError("<" + "x" * 1800)

    monkeypatch.setattr(wizard, "_store_ready_srt", fail)
    update, message = _update(text="bad srt")
    with pytest.raises(ApplicationHandlerStop):
        _run(wizard.handle_dub_wizard_text, update, context)
    text, kwargs = message.replies[0]
    assert text.startswith("⚠️ SRT не принят: &lt;")
    assert len(text) < 1650
    assert kwargs == {"parse_mode": "HTML"}


class TelegramFile:
    def __init__(self, payload):
        self.payload = payload

    async def download_as_bytearray(self):
        return bytearray(self.payload)


class Bot:
    def __init__(self, payload):
        self.payload = payload
        self.file_ids = []

    async def get_file(self, file_id):
        self.file_ids.append(file_id)
        return TelegramFile(self.payload)


def test_document_handler_noop_and_admin_guards(monkeypatch):
    update, _ = _update(with_user=False)
    _run(wizard.handle_dub_wizard_document, update, _context(state=None))

    update, _ = _update()
    _run(wizard.handle_dub_wizard_document, update, _context())

    document = SimpleNamespace(file_name="notes.txt", file_id="file")
    update, _ = _update(document=document)
    _run(wizard.handle_dub_wizard_document, update, _context())

    document = SimpleNamespace(file_name="ready.srt", file_id="file")
    update, message = _update(user_id=2, document=document)
    _run(wizard.handle_dub_wizard_document, update, _context())
    assert message.replies[0][0].startswith("⛔")

    monkeypatch.setattr(wizard, "_latest_direct_draft", lambda _user_id: None)
    update, _message = _update(document=document)
    _run(wizard.handle_dub_wizard_document, update, _context())


def test_document_handler_rejects_non_admin_and_wrong_suffix():
    state = {wizard._WIZARD_KEY: {"awaiting": "srt", "project_id": "p"}}
    document = SimpleNamespace(file_name="ready.srt", file_id="file")
    update, _message = _update(user_id=2, document=document)
    with pytest.raises(ApplicationHandlerStop):
        _run(wizard.handle_dub_wizard_document, update, _context(state=dict(state)))

    document = SimpleNamespace(file_name="notes.txt", file_id="file")
    update, message = _update(document=document)
    with pytest.raises(ApplicationHandlerStop):
        _run(wizard.handle_dub_wizard_document, update, _context(state=dict(state)))
    assert "Нужен именно" in message.replies[0][0]


def test_document_handler_auto_binds_draft_and_stores_srt(monkeypatch):
    monkeypatch.setattr(wizard, "_latest_direct_draft", lambda _user_id: {"id": "p"})
    stored = []

    async def store(_update, context, text):
        stored.append((context.user_data[wizard._WIZARD_KEY], text))

    monkeypatch.setattr(wizard, "_store_ready_srt", store)
    payload = b"1\n00:00:00,000 --> 00:00:01,000\nReady\n"
    bot = Bot(payload)
    document = SimpleNamespace(file_name="READY.SRT", file_id="file-id")
    update, _message = _update(document=document)
    context = _context(state={}, bot=bot)

    with pytest.raises(ApplicationHandlerStop):
        _run(wizard.handle_dub_wizard_document, update, context)

    assert bot.file_ids == ["file-id"]
    assert stored == [
        (
            {"awaiting": "srt", "mode": "direct", "project_id": "p"},
            payload.decode("utf-8"),
        )
    ]


def test_document_handler_rejects_oversize_file(monkeypatch):
    def decode_must_not_run(_payload):
        raise AssertionError("oversize payload must not be decoded")

    monkeypatch.setattr(wizard, "_decode_text_file", decode_must_not_run)
    bot = Bot(b"x" * 2_000_001)
    document = SimpleNamespace(file_name="ready.srt", file_id="file-id")
    update, message = _update(document=document)
    context = _context(
        state={wizard._WIZARD_KEY: {"awaiting": "srt", "project_id": "p"}},
        bot=bot,
    )

    with pytest.raises(ApplicationHandlerStop):
        _run(wizard.handle_dub_wizard_document, update, context)

    assert "максимум 2 МБ" in message.replies[0][0]


def test_wizard_registration_is_idempotent_and_ordered():
    class Application:
        def __init__(self):
            self.bot_data = {}
            self.registrations = []

        def add_handler(self, handler, group):
            self.registrations.append((handler, group))

    application = Application()
    wizard.register_dub_wizard_handlers(application)
    wizard.register_dub_wizard_handlers(application)

    assert len(application.registrations) == 8
    assert [group for _handler, group in application.registrations] == [
        0,
        -60,
        -60,
        -60,
        -60,
        -60,
        -59,
        -59,
    ]
    assert application.bot_data["dub_studio_wizard_registered"] is True
