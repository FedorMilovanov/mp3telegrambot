"""Regression tests for v3 patch 55 — Telegram command menu completeness."""

from pathlib import Path


def test_admin_bot_command_menu_includes_new_archive_repair_segment_health_commands():
    src = Path("main.py").read_text(encoding="utf-8")
    for command in [
        "archive",
        "search",
        "segments",
        "cutseg",
        "repairpage",
        "archivefile",
        "segmentfile",
        "prompthealth",
        "codehealth",
    ]:
        assert f'BotCommand("{command}"' in src


def test_all_registered_command_handlers_have_menu_or_are_intentionally_secondary():
    src = Path("main.py").read_text(encoding="utf-8")
    registered = set()
    for line in src.splitlines():
        if 'CommandHandler("' in line:
            registered.add(line.split('CommandHandler("', 1)[1].split('"', 1)[0])
    menu = set()
    for line in src.splitlines():
        if 'BotCommand("' in line:
            menu.add(line.split('BotCommand("', 1)[1].split('"', 1)[0])
    intentionally_secondary = {"lastpages", "author", "scripture", "repairrecent"}
    missing = registered - menu - intentionally_secondary
    assert not missing
