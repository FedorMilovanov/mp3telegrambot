"""Behavior contracts for one-command Dub Studio production startup."""

import asyncio
from types import SimpleNamespace

import handlers.dub_quickstart as quickstart


class Message:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


def _update(user_id=1, *, with_message=True, with_user=True):
    message = Message() if with_message else None
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id) if with_user else None,
        effective_message=message,
        effective_chat=SimpleNamespace(id=777),
    )
    return update, message


def _run(update, *, args=None):
    context = SimpleNamespace(args=args)
    asyncio.run(quickstart.dubstart_command(update, context))


def test_recipe_help_handles_empty_and_escapes_bounded_titles(monkeypatch):
    monkeypatch.setattr(quickstart, "list_recipes", lambda: [])
    assert quickstart._recipe_help() == "Рецепты пока не зарегистрированы."

    recipes = [SimpleNamespace(recipe_id="<recipe>", title="<" + "x" * 200)]
    monkeypatch.setattr(quickstart, "list_recipes", lambda: recipes)
    result = quickstart._recipe_help()

    assert "<code>&lt;recipe&gt;</code>" in result
    assert "&lt;" + "x" * 149 in result
    assert "x" * 151 not in result


def test_unauthorized_and_missing_users_are_rejected_safely(monkeypatch):
    monkeypatch.setattr(quickstart, "ADMIN_IDS", {1})
    update, message = _update(user_id=2)
    _run(update)
    assert message.replies == [("⛔ Dub Studio доступна только администратору.", {})]

    update, _message = _update(with_user=False, with_message=False)
    _run(update)


def test_admin_without_message_or_chat_returns_without_store_work(monkeypatch):
    monkeypatch.setattr(quickstart, "ADMIN_IDS", {1})
    update, message = _update(with_message=False)
    _run(update, args=[])
    assert message is None

    update, message = _update()
    update.effective_chat = None
    _run(update, args=["recipe"])
    assert message.replies == []


def test_missing_recipe_prints_usage_and_recipe_help(monkeypatch):
    monkeypatch.setattr(quickstart, "ADMIN_IDS", {1})
    monkeypatch.setattr(quickstart, "list_recipes", lambda: [])
    update, message = _update()

    _run(update, args=[])

    text, kwargs = message.replies[0]
    assert "/dubstart RECIPE" in text
    assert "Рецепты пока не зарегистрированы" in text
    assert kwargs == {"parse_mode": "HTML"}


def test_admin_starts_render_job_and_returns_status_commands(monkeypatch):
    monkeypatch.setattr(quickstart, "ADMIN_IDS", {1})
    calls = []

    class Store:
        def create_project(self, recipe_id, **kwargs):
            calls.append(("create", recipe_id, kwargs))
            return {"id": "project<&>"}

        def enqueue_job(self, project_id, kind):
            calls.append(("enqueue", project_id, kind))
            return {"id": 42}

    monkeypatch.setattr(quickstart, "DubStore", Store)
    update, message = _update(user_id=1)

    _run(update, args=[" <RECIPE> "])

    assert calls == [
        (
            "create",
            "<recipe>",
            {"owner_user_id": 1, "owner_chat_id": 777},
        ),
        ("enqueue", "project<&>", "render"),
    ]
    text, kwargs = message.replies[0]
    assert "project&lt;&amp;&gt;" in text
    assert "<code>&lt;recipe&gt;</code>" in text
    assert "<b>#42</b>" in text
    assert "/dubstatus project&lt;&amp;&gt;" in text
    assert "/dubsend project&lt;&amp;&gt;" in text
    assert kwargs == {"parse_mode": "HTML"}


def test_startup_error_is_escaped_and_bounded(monkeypatch):
    monkeypatch.setattr(quickstart, "ADMIN_IDS", {1})

    class Store:
        def __init__(self):
            raise RuntimeError("<" + "x" * 1000)

    monkeypatch.setattr(quickstart, "DubStore", Store)
    update, message = _update()

    _run(update, args=["recipe"])

    text, kwargs = message.replies[0]
    assert text.startswith("❌ Не удалось запустить Dub Studio: &lt;")
    assert len(text) < 950
    assert kwargs == {"parse_mode": "HTML"}


def test_registration_is_idempotent():
    class Application:
        def __init__(self):
            self.bot_data = {}
            self.handlers = []

        def add_handler(self, handler):
            self.handlers.append(handler)

    application = Application()
    quickstart.register_dub_quickstart_handler(application)
    quickstart.register_dub_quickstart_handler(application)

    assert len(application.handlers) == 1
    assert application.bot_data["dub_studio_quickstart_registered"] is True
    handler = application.handlers[0]
    assert handler.commands == frozenset({"dubstart"})
    assert handler.callback is quickstart.dubstart_command
