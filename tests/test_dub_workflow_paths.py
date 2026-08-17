from __future__ import annotations

import re
from pathlib import Path


WORKFLOWS = (
    Path(".github/workflows/dub-clean-expressive-check.yml"),
    Path(".github/workflows/dub-facade-hardening-check.yml"),
)

_EXPLICIT_PY_PATH = re.compile(
    r"(?<![\w./-])((?:bot_new\.py|(?:services|handlers|tools|tests)/[^\s'\"\\]+\.py))"
)


def _explicit_python_paths(workflow: Path) -> list[str]:
    text = workflow.read_text(encoding="utf-8")
    return sorted(
        {
            match.group(1).rstrip("\\")
            for match in _EXPLICIT_PY_PATH.finditer(text)
            if "*" not in match.group(1)
        }
    )


def test_dub_workflow_explicit_python_paths_exist() -> None:
    for workflow in WORKFLOWS:
        paths = _explicit_python_paths(workflow)
        assert paths, f"{workflow}: no explicit Python paths found"
        missing = [path for path in paths if not Path(path).is_file()]
        assert not missing, f"{workflow}: stale Python paths: {missing}"


def test_dub_workflows_run_on_pull_requests_and_use_current_actions() -> None:
    for workflow in WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        assert "pull_request:" in text, f"{workflow}: PR gate is missing"
        assert "actions/checkout@v6" in text
        assert "actions/setup-python@v6" in text
        assert "actions/checkout@v4" not in text
        assert "actions/setup-python@v5" not in text
