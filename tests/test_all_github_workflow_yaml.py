import re
from pathlib import Path

import yaml

from tools.workflow_actions import (
    MINIMUM_ACTION_MAJORS,
    action_major,
    action_refs,
    below_floor,
    minimum_major,
)


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
LITERAL_REPO_PYTHON_PATH_RE = re.compile(
    r"(?<![\w./-])((?:core|handlers|pipelines|scripts|services|tests|tools)/"
    r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.py)(?![\w./-])"
)


def _workflow_paths() -> list[Path]:
    return sorted(
        [*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")],
        key=lambda path: path.as_posix(),
    )


def test_every_github_actions_workflow_is_valid_and_structured_yaml() -> None:
    workflow_paths = _workflow_paths()
    assert workflow_paths, "No GitHub Actions workflow files were found."

    failures: list[str] = []
    for path in workflow_paths:
        relative = path.relative_to(ROOT)
        try:
            source = path.read_text(encoding="utf-8")
            document = yaml.safe_load(source)
            literal_keys = yaml.load(source, Loader=yaml.BaseLoader)
        except (UnicodeError, OSError, yaml.YAMLError) as exc:
            failures.append(f"{relative}: {type(exc).__name__}: {exc}")
            continue

        if not isinstance(document, dict) or not isinstance(literal_keys, dict):
            failures.append(f"{relative}: top-level YAML document must be a mapping")
            continue
        if "on" not in literal_keys:
            failures.append(f"{relative}: missing top-level 'on' trigger mapping")
        jobs = literal_keys.get("jobs")
        if not isinstance(jobs, dict) or not jobs:
            failures.append(f"{relative}: missing non-empty top-level 'jobs' mapping")

    assert not failures, "Invalid GitHub Actions workflow YAML:\n" + "\n".join(failures)


def test_literal_repo_python_paths_referenced_by_workflows_exist() -> None:
    workflow_paths = _workflow_paths()
    assert workflow_paths, "No GitHub Actions workflow files were found."

    failures: list[str] = []
    for workflow_path in workflow_paths:
        source = workflow_path.read_text(encoding="utf-8")
        relative_workflow = workflow_path.relative_to(ROOT)
        references = sorted(set(LITERAL_REPO_PYTHON_PATH_RE.findall(source)))
        for reference in references:
            if not (ROOT / reference).is_file():
                failures.append(
                    f"{relative_workflow}: missing referenced Python path {reference}"
                )

    assert not failures, "GitHub Actions workflows reference missing Python paths:\n" + "\n".join(
        failures
    )


def test_workflow_actions_meet_the_node_runtime_floor() -> None:
    """A too-old action major is a real failure; a *newer* one is not."""
    failures: list[str] = []
    for path in _workflow_paths():
        for problem in below_floor(path.read_text(encoding="utf-8")):
            failures.append(f"{path.relative_to(ROOT)}: {problem}")
    assert not failures, "GitHub Actions below the supported floor:\n" + "\n".join(
        failures
    )


def test_action_floor_helper_accepts_newer_majors() -> None:
    text = "      - uses: actions/checkout@v7\n      - uses: actions/setup-python@v8\n"
    assert below_floor(text) == []
    assert minimum_major(text, "actions/checkout") == 7


def test_action_floor_helper_rejects_older_majors() -> None:
    text = "      - uses: actions/checkout@v4\n      - uses: actions/setup-python@v5.1.0\n"
    assert below_floor(text) == [
        "actions/checkout@v4 is below the v6 floor",
        "actions/setup-python@v5.1.0 is below the v6 floor",
    ]


def test_action_floor_helper_parses_quotes_comments_and_shas() -> None:
    text = (
        '      - uses: "actions/checkout@v7"  # pinned major\n'
        "      - uses: actions/upload-artifact@0123456789abcdef0123456789abcdef01234567\n"
    )
    refs = action_refs(text)
    assert refs["actions/checkout"] == {"v7"}
    sha_ref = next(iter(refs["actions/upload-artifact"]))
    assert action_major(sha_ref) is None, "a sha pin has no comparable major"
    assert below_floor(text) == []


def test_every_floored_action_is_actually_used_somewhere() -> None:
    used: set[str] = set()
    for path in _workflow_paths():
        used.update(action_refs(path.read_text(encoding="utf-8")))
    unused = sorted(set(MINIMUM_ACTION_MAJORS) - used)
    assert unused == [], f"floors for unused actions: {unused}"
