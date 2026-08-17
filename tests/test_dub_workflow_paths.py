from __future__ import annotations

import glob
import re
from pathlib import Path


WORKFLOWS = (
    Path(".github/workflows/dub-clean-expressive-check.yml"),
    Path(".github/workflows/dub-facade-hardening-check.yml"),
)

_EXPLICIT_PY_PATH = re.compile(
    r"(?<![\w./-])((?:bot_new\.py|(?:services|handlers|tools|tests)/[^\s'\"\\]+\.py))"
)
_YAML_QUOTED_LIST_ITEM = re.compile(r'^\s*-\s+"([^"]+)"\s*$', re.MULTILINE)
_REPO_PATH_PREFIXES = (".github/", "bot_new.py", "handlers/", "services/", "tests/", "tools/")


def _explicit_python_paths(workflow: Path) -> list[str]:
    text = workflow.read_text(encoding="utf-8")
    return sorted(
        {
            match.group(1).rstrip("\\")
            for match in _EXPLICIT_PY_PATH.finditer(text)
            if "*" not in match.group(1)
        }
    )


def _trigger_path_patterns(workflow: Path) -> list[str]:
    text = workflow.read_text(encoding="utf-8")
    return sorted(
        {
            value
            for value in _YAML_QUOTED_LIST_ITEM.findall(text)
            if value.startswith(_REPO_PATH_PREFIXES)
        }
    )


def test_dub_workflow_explicit_python_paths_exist() -> None:
    missing_by_workflow: dict[str, list[str]] = {}
    for workflow in WORKFLOWS:
        paths = _explicit_python_paths(workflow)
        assert paths, f"{workflow}: no explicit Python paths found"
        missing = [path for path in paths if not Path(path).is_file()]
        if missing:
            missing_by_workflow[str(workflow)] = missing

    assert not missing_by_workflow, f"stale Dub workflow Python paths: {missing_by_workflow}"


def test_dub_workflow_trigger_paths_match_repository() -> None:
    stale_by_workflow: dict[str, list[str]] = {}
    for workflow in WORKFLOWS:
        patterns = _trigger_path_patterns(workflow)
        assert patterns, f"{workflow}: no repository trigger paths found"
        stale = []
        for pattern in patterns:
            if glob.has_magic(pattern):
                if not glob.glob(pattern, recursive=True):
                    stale.append(pattern)
            elif not Path(pattern).exists():
                stale.append(pattern)
        if stale:
            stale_by_workflow[str(workflow)] = stale

    assert not stale_by_workflow, f"stale Dub workflow trigger paths: {stale_by_workflow}"


def test_dub_workflows_run_on_pull_requests_and_use_current_actions() -> None:
    for workflow in WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        assert "pull_request:" in text, f"{workflow}: PR gate is missing"
        assert "actions/checkout@v6" in text
        assert "actions/setup-python@v6" in text
        assert "actions/checkout@v4" not in text
        assert "actions/setup-python@v5" not in text


def test_dub_workflows_run_pytest_as_python_module() -> None:
    for workflow in WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        assert "python -m pytest -q" in text, f"{workflow}: pytest must run via python -m"
        assert "\n          pytest -q" not in text, f"{workflow}: bare pytest can lose repo import root"
