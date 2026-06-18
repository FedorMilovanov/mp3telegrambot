"""Regression tests for repository agent/maintenance contract."""

from pathlib import Path


def test_agents_contract_exists_and_covers_quality_policy():
    text = Path("AGENTS.md").read_text(encoding="utf-8")

    required = [
        "Repair/log/history first",
        "CONTENT_AUDIT_BLOCK_PUBLICATION=1",
        "PAGE_AUDIT_BLOCK_PUBLICATION=1",
        "AUDIT_BLOCK_PUBLICATION=1",
        "python tools/verify_repo.py",
        "docs/quality_audit_history.md",
        "prompthealth",
        "No secret leakage",
        "Telegram output safety",
        "Quiz/test quality",
    ]
    for needle in required:
        assert needle in text


def test_all_async_command_handlers_are_registered():
    import ast
    import re

    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(commands)
    command_funcs = {
        node.name
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name.endswith("_command")
        and not node.name.startswith("_")
    }
    registered_funcs = {
        match.group(1)
        for match in re.finditer(r'CommandHandler\("[^"]+"\s*,\s*([A-Za-z_][A-Za-z0-9_]*)', main)
    }

    missing = command_funcs - registered_funcs
    assert missing == set()
