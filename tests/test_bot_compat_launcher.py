import runpy

import bot


def test_compat_launcher_executes_bot_new_as_main(monkeypatch):
    calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(bot, "_print_banner", lambda: None)
    monkeypatch.setattr(
        runpy,
        "run_module",
        lambda module_name, *, run_name=None: calls.append((module_name, run_name)),
    )

    bot.main()

    assert calls == [("bot_new", "__main__")]
