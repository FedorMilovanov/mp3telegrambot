"""Executed navigation and profile-selection contracts for Dub Studio wizard."""

import asyncio
from types import SimpleNamespace

import pytest

import handlers.dub_commands as dub_commands
from handlers import dub_wizard as wizard


@pytest.fixture(autouse=True)
def _admin_ids(monkeypatch):
    monkeypatch.setattr(wizard, "ADMIN_IDS", {1})


def _choice(profile_id="profile", *, default=False):
    return SimpleNamespace(
        profile_id=profile_id,
        display_name=f"<{profile_id}>",
        backend_id="voxcpm2",
        model_family="family",
        model_revision="revision-1",
        source_sha256="",
        source_kind="runtime",
        fingerprint="f" * 64,
        is_default=default,
    )


def _choices(count=2):
    return tuple(_choice(f"profile-{index}", default=index == 0) for index in range(count))


def _patch_choices(monkeypatch, choices):
    monkeypatch.setattr(wizard, "production_tts_profile_choices", lambda: choices)
    monkeypatch.setattr(
        wizard,
        "production_tts_profile_choice",
        lambda profile_id: next(choice for choice in choices if choice.profile_id == profile_id),
    )


class Message:
    def __init__(self, *, text="", document=None):
        self.text = text
        self.document = document
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class Query:
    def __init__(self, data, message):
        self.data = data
        self.message = message
        self.answers = 0
        self.edits = []

    async def answer(self):
        self.answers += 1

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


def _update(*, user_id=1, with_user=True, with_message=True, data=None, with_query=False):
    message = Message() if with_message else None
    query = Query(data, message) if with_query else None
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id) if with_user else None,
        effective_message=message,
        effective_chat=SimpleNamespace(id=777),
        callback_query=query,
    )
    return update, query if with_query else message


def _context(*, args=None, state=None):
    return SimpleNamespace(args=args, user_data={} if state is None else state)


def _run(callback, *args):
    return asyncio.run(callback(*args))


def test_project_root_is_contained_and_created(monkeypatch, tmp_path):
    monkeypatch.setattr(wizard, "studio_root", lambda: tmp_path)

    root = wizard._project_root("project")

    assert root == tmp_path / "projects" / "project"
    assert root.is_dir()
    with pytest.raises(ValueError):
        wizard._project_root("../outside")


def test_catalog_and_selection_pagination_and_stale_guards(monkeypatch):
    choices = _choices(7)
    _patch_choices(monkeypatch, choices)

    first, page, page_count = wizard._catalog_page(-10)
    last, last_page, last_count = wizard._catalog_page(99)
    assert (len(first), page, page_count) == (6, 0, 2)
    assert (len(last), last_page, last_count) == (1, 1, 2)
    text, page, page_count = wizard._catalog_text(1)
    assert "Страница <b>2/2</b>" in text
    assert "profile-6" in text
    assert "profile-0" not in text

    forward = wizard._catalog_keyboard(0, 2)
    backward = wizard._catalog_keyboard(1, 2)
    assert forward.inline_keyboard[0][0].callback_data == "dubwiz|catalog|1"
    assert backward.inline_keyboard[0][0].callback_data == "dubwiz|catalog|0"

    state = wizard._selection_state("direct")
    state["selection_token"] = "deadbeef"
    text, page, page_count = wizard._selection_text(state, 1)
    assert "Режим: <b>готовый SRT</b>" in text
    assert (page, page_count) == (1, 2)
    first_keyboard = wizard._selection_keyboard(state, 0)
    second_keyboard = wizard._selection_keyboard(state, 1)
    assert first_keyboard.inline_keyboard[-2][0].callback_data.endswith(":1")
    assert second_keyboard.inline_keyboard[-2][0].callback_data.endswith(":0")

    with pytest.raises(RuntimeError, match="selection state пуст"):
        wizard._selection_page({}, 0)
    with pytest.raises(RuntimeError, match="catalog изменился"):
        wizard._selection_page({"profile_ids": ["missing"]}, 0)
    with pytest.raises(RuntimeError, match="token повреждён"):
        wizard._selection_keyboard({**state, "selection_token": "bad"}, 0)
    with pytest.raises(RuntimeError, match="Некорректный индекс"):
        wizard._parse_selection_value(state, "deadbeef:not-an-int")


def test_selection_keyboard_rejects_oversized_defensive_callback(monkeypatch):
    choice = _choice()
    monkeypatch.setattr(
        wizard,
        "_selection_page",
        lambda _state, _page: ((choice,), 10**60, 0, 1),
    )

    with pytest.raises(RuntimeError, match="Telegram limit"):
        wizard._selection_keyboard({"selection_token": "deadbeef", "profile_ids": ["profile"]}, 0)


def test_mode_text_keeps_gemini_and_direct_flows_distinct():
    choice = _choice()

    gemini = wizard._mode_text("gemini", choice)
    direct = wizard._mode_text("direct", choice)

    assert "Gemini MAX" in gemini
    assert "три редакторских прохода" in gemini
    assert "SRT без изменений" in direct
    assert "Gemini его не проверяет" in direct
    assert "&lt;profile&gt;" in gemini
    assert "&lt;profile&gt;" in direct


def test_env_size_mode_and_legacy_converter_validation(monkeypatch):
    monkeypatch.setenv("DUB_TTS_OPTIONS_JSON", "x" * (wizard._MAX_ENV_JSON_CHARS + 1))
    with pytest.raises(ValueError, match="слишком большой"):
        wizard._env_json_object("DUB_TTS_OPTIONS_JSON")

    with pytest.raises(ValueError, match="Неизвестный режим"):
        wizard._request_payload("video", "url", "unknown", "profile")

    choice = _choice()
    monkeypatch.setattr(wizard, "production_tts_profile_choice", lambda _value: choice)
    monkeypatch.setenv("DUB_TTS_OPTIONS_JSON", "")
    monkeypatch.setenv("DUB_TTS_BACKEND_CONFIG_JSON", "")
    monkeypatch.setenv("DUB_VOX_THREADS", "not-an-int")
    with pytest.raises(ValueError, match="Некорректный DUB_VOX_THREADS"):
        wizard._request_payload("video", "url", "gemini", "profile")


def test_admin_gate_and_latest_direct_draft_selection(monkeypatch, tmp_path):
    update, _ = _update(user_id=1)
    assert _run(wizard._admin, update) is True
    update, message = _update(user_id=2)
    assert _run(wizard._admin, update) is False
    assert message.replies[0][0].startswith("⛔")
    update, _ = _update(with_user=False, with_message=False)
    assert _run(wizard._admin, update) is False

    projects = [
        {"id": "other", "recipe_id": "other", "metadata": {}, "status": "draft"},
        {
            "id": "gemini",
            "recipe_id": "generic_short_v1",
            "metadata": {"translation_mode": "gemini"},
            "status": "draft",
        },
        {
            "id": "ready",
            "recipe_id": "generic_short_v1",
            "metadata": {"translation_mode": "direct"},
            "status": "draft",
        },
        {
            "id": "waiting",
            "recipe_id": "generic_short_v1",
            "metadata": {"translation_mode": "direct"},
            "status": "failed",
            "owner_user_id": 1,
        },
    ]

    class Store:
        def list_projects(self, *, owner_user_id, limit):
            assert (owner_user_id, limit) == (1, 10)
            return projects

    monkeypatch.setattr(wizard, "DubStore", Store)
    monkeypatch.setattr(wizard, "_project_root", lambda project_id: tmp_path / project_id)
    ready = tmp_path / "ready" / "input" / "ready_translation.srt"
    ready.parent.mkdir(parents=True)
    ready.touch()

    assert wizard._latest_direct_draft(1)["id"] == "waiting"
    projects.clear()
    assert wizard._latest_direct_draft(1) is None


def test_unauthorized_commands_return_before_work():
    update, message = _update(user_id=2)

    _run(wizard.dub_home_command, update, _context(args=[]))
    _run(wizard.dubtts_command, update, _context(args=[]))
    _run(wizard.dubsrt_command, update, _context(args=[]))

    assert len(message.replies) == 3
    assert all(text.startswith("⛔") for text, _kwargs in message.replies)


def test_home_command_delegates_legacy_args_and_renders_default(monkeypatch):
    calls = []

    async def legacy(update, context):
        calls.append((update, context))

    monkeypatch.setattr(dub_commands, "dub_command", legacy)
    update, _message = _update()
    legacy_context = _context(args=["legacy"])
    _run(wizard.dub_home_command, update, legacy_context)
    assert calls == [(update, legacy_context)]

    choices = _choices()
    _patch_choices(monkeypatch, choices)
    context = _context(args=[], state={wizard._WIZARD_KEY: {"awaiting": "url"}})
    _run(wizard.dub_home_command, update, context)

    assert context.user_data == {}
    text, kwargs = update.effective_message.replies[0]
    assert "Default: <b>&lt;profile-0&gt;</b>" in text
    assert "revision-1" in text
    assert kwargs["parse_mode"] == "HTML"


def test_newvideo_alias_delegates_to_home(monkeypatch):
    calls = []

    async def home(update, context):
        calls.append((update, context))

    monkeypatch.setattr(wizard, "dub_home_command", home)
    update, _ = _update()
    context = _context()
    _run(wizard.dubnewvideo_command, update, context)
    assert calls == [(update, context)]


def test_dubtts_catalog_and_usage_paths(monkeypatch):
    _patch_choices(monkeypatch, _choices())
    update, message = _update()

    _run(wizard.dubtts_command, update, _context(args=[]))
    _run(wizard.dubtts_command, update, _context(args=["only-one"]))

    assert "Production TTS-модели" in message.replies[0][0]
    assert "Использование:" in message.replies[1][0]


@pytest.mark.parametrize("changed", [True, False])
def test_dubtts_rebinds_inactive_project(monkeypatch, tmp_path, changed):
    choices = _choices()
    _patch_choices(monkeypatch, choices)
    store = SimpleNamespace(root=tmp_path)
    monkeypatch.setattr(wizard, "DubStore", lambda: store)
    calls = {}

    def rebind(actual_store, project_id, **kwargs):
        calls["rebind"] = (actual_store, project_id, kwargs)
        return SimpleNamespace(
            choice=SimpleNamespace(profile_id="profile-0"),
            project_id=project_id,
            changed=changed,
        )

    monkeypatch.setattr(wizard, "rebind_inactive_project_tts_profile", rebind)
    update, message = _update()

    _run(
        wizard.dubtts_command,
        update,
        _context(args=[" PROJECT ", "profile-0"]),
    )

    actual_store, project_id, kwargs = calls["rebind"]
    assert actual_store is store
    assert project_id == "project"
    assert kwargs["owner_user_id"] == 1
    assert kwargs["profile_value"] == "profile-0"
    assert kwargs["request_path"] == (tmp_path / "projects" / "project" / "request.json")
    text = message.replies[0][0]
    assert ("Профиль закреплён:" in text) if changed else ("уже был закреплён" in text)


def test_dubtts_reports_escaped_bounded_rebind_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(wizard, "DubStore", lambda: SimpleNamespace(root=tmp_path))

    def fail(*_args, **_kwargs):
        raise RuntimeError("<" + "x" * 1600)

    monkeypatch.setattr(wizard, "rebind_inactive_project_tts_profile", fail)
    update, message = _update()

    _run(wizard.dubtts_command, update, _context(args=["p", "profile"]))

    text, kwargs = message.replies[0]
    assert text.startswith("⚠️ TTS-профиль не изменён: &lt;")
    assert len(text) < 1450
    assert kwargs == {"parse_mode": "HTML"}


def test_show_projects_handles_empty_message_and_rich_callback(monkeypatch):
    class Store:
        projects = []

        def list_projects(self, *, owner_user_id, limit):
            assert (owner_user_id, limit) == (1, 10)
            return self.projects

    store = Store()
    monkeypatch.setattr(wizard, "DubStore", lambda: store)
    update, _ = _update()
    _run(wizard._show_projects, update)
    assert "Проектов пока нет" in update.effective_message.replies[0][0]

    store.projects = [
        {
            "id": "g<&>",
            "title": "<Gemini>",
            "status": "draft",
            "progress": 10,
            "metadata": {
                "translation_mode": "gemini",
                "speech_model_profile": "profile-g",
            },
        },
        {
            "id": "d",
            "title": "Direct",
            "status": "failed",
            "metadata": {"translation_mode": "direct"},
        },
        {
            "id": "r",
            "title": "Recipe",
            "status": "queued",
            "metadata": {},
        },
    ]
    update, query = _update(with_query=True, data="dubwiz|projects|list")
    _run(wizard._show_projects, update)

    text = query.edits[0][0]
    assert "g&lt;&amp;&gt;" in text
    assert "&lt;Gemini&gt;" in text
    assert "Gemini MAX" in text
    assert "готовый SRT" in text
    assert "recipe" in text
    assert "legacy/default" in text
    assert query.edits[0][1]["parse_mode"] == "HTML"


def test_activate_srt_upload_validates_project_and_sets_state(monkeypatch):
    project = {
        "id": "p",
        "owner_user_id": 1,
        "status": "draft",
        "metadata": {"translation_mode": "direct"},
    }

    class Store:
        def get_project(self, project_id):
            assert project_id == "p"
            return project

    monkeypatch.setattr(wizard, "DubStore", Store)
    update, message = _update()
    context = _context()
    _run(wizard._activate_srt_upload, update, context, "p")

    assert context.user_data[wizard._WIZARD_KEY] == {
        "awaiting": "srt",
        "mode": "direct",
        "project_id": "p",
    }
    assert "Пришлите готовый русский SRT" in message.replies[0][0]

    base = dict(project)
    for changes, error, match in (
        ({"owner_user_id": 2}, PermissionError, "не ваш проект"),
        ({"metadata": {"translation_mode": "gemini"}}, RuntimeError, "Gemini MAX"),
        ({"status": "queued"}, RuntimeError, "уже выполняется"),
    ):
        project.clear()
        project.update({**base, **changes})
        with pytest.raises(error, match=match):
            _run(wizard._activate_srt_upload, update, context, "p")


def test_callback_profile_lifecycle(monkeypatch):
    choices = _choices()
    _patch_choices(monkeypatch, choices)
    update, query = _update(with_query=True, data="dubwiz|mode|direct")
    context = _context()

    _run(wizard.handle_dub_wizard_callback, update, context)
    state = context.user_data[wizard._WIZARD_KEY]
    token = state["selection_token"]
    assert state["awaiting"] == "tts_profile"

    query.data = f"dubwiz|profile_page|{token}:0"
    _run(wizard.handle_dub_wizard_callback, update, context)
    query.data = f"dubwiz|profile|{token}:0"
    _run(wizard.handle_dub_wizard_callback, update, context)

    state = context.user_data[wizard._WIZARD_KEY]
    assert state["awaiting"] == "url"
    assert state["profile_id"] == "profile-0"
    assert state["profile_fingerprint"] == "f" * 64
    assert "selection_token" not in state
    assert "profile_ids" not in state
    assert query.answers == 3
    assert len(query.edits) == 3
    assert "SRT без изменений" in query.edits[-1][0]


def test_callback_catalog_home_projects_worker_and_srt_routes(monkeypatch):
    _patch_choices(monkeypatch, _choices())

    class Store:
        def list_projects(self, **_kwargs):
            return []

    monkeypatch.setattr(wizard, "DubStore", Store)
    worker_calls = []
    srt_calls = []

    async def worker(update, context):
        worker_calls.append((update, context))

    async def activate(_update, _context, project_id):
        srt_calls.append(project_id)

    monkeypatch.setattr(dub_commands, "dubworker_command", worker)
    monkeypatch.setattr(wizard, "_activate_srt_upload", activate)
    update, query = _update(with_query=True, data="dubwiz|catalog|0")
    context = _context(state={wizard._WIZARD_KEY: {"awaiting": "url"}})

    for data in (
        "dubwiz|catalog|0",
        "dubwiz|home|show",
        "dubwiz|projects|list",
        "dubwiz|worker|status",
        "dubwiz|srt|p",
        "dubwiz|translation|p",
    ):
        query.data = data
        _run(wizard.handle_dub_wizard_callback, update, context)

    assert query.answers == 6
    assert len(query.edits) == 3
    assert worker_calls == [(update, context)]
    assert srt_calls == ["p", "p"]
    assert context.user_data == {}


def test_callback_noop_admin_and_validation_paths(monkeypatch):
    update, _ = _update()
    _run(wizard.handle_dub_wizard_callback, update, _context())

    update, query = _update(user_id=2, with_query=True, data="dubwiz|home|show")
    _run(wizard.handle_dub_wizard_callback, update, _context())
    assert query.answers == 1
    assert query.edits == []
    assert update.effective_message.replies[0][0].startswith("⛔")

    update, query = _update(with_query=True, data="not-a-wizard-callback")
    context = _context()
    _run(wizard.handle_dub_wizard_callback, update, context)
    assert query.edits == []

    for data in ("dubwiz|profile_page|deadbeef:0", "dubwiz|profile|deadbeef:0"):
        query.data = data
        _run(wizard.handle_dub_wizard_callback, update, context)

    context.user_data[wizard._WIZARD_KEY] = {
        "awaiting": "tts_profile",
        "selection_token": "deadbeef",
        "profile_ids": ["profile"],
        "mode": "direct",
    }
    query.data = "dubwiz|profile|deadbeef:2"
    _run(wizard.handle_dub_wizard_callback, update, context)
    query.data = "dubwiz|unknown|value"
    _run(wizard.handle_dub_wizard_callback, update, context)

    errors = [text for text, _kwargs in update.effective_message.replies]
    assert sum("Выбор TTS уже завершён" in text for text in errors) == 2
    assert any("вышел за границы" in text for text in errors)


def test_missing_telegram_context_guards_fail_closed(monkeypatch):
    missing_message, _ = _update(with_message=False)
    _run(wizard.dub_home_command, missing_message, _context())
    _run(wizard.dubtts_command, missing_message, _context())
    _run(wizard.dubsrt_command, missing_message, _context())
    _run(wizard._activate_srt_upload, missing_message, _context(), "project")

    missing_user, _ = _update(with_user=False)
    _run(wizard._show_projects, missing_user)

    class EmptyStore:
        def list_projects(self, **_kwargs):
            return []

    monkeypatch.setattr(wizard, "DubStore", EmptyStore)
    _run(wizard._show_projects, missing_message)

    no_user_data = SimpleNamespace(args=None, user_data=None)
    update, _ = _update()
    _run(wizard.handle_dub_wizard_callback, update, no_user_data)
    with pytest.raises(RuntimeError, match="Контекст выбора TTS"):
        _run(wizard._show_profile_selection, SimpleNamespace(), no_user_data, "direct")
    with pytest.raises(RuntimeError, match="create a Dub project"):
        _run(
            wizard._create_generic_project,
            missing_message,
            _context(),
            "https://youtu.be/video-id",
            "direct",
            "profile",
        )
    with pytest.raises(RuntimeError, match="store SRT"):
        _run(wizard._store_ready_srt, missing_message, _context(), "1\n00:00 --> 00:01\ntext")
