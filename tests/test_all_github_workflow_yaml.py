import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
LITERAL_REPO_PYTHON_PATH_RE = re.compile(
    r"(?<![\w./-])((?:core|handlers|pipelines|scripts|services|tests|tools)/"
    r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.py)(?![\w./-])"
)
USES_RE = re.compile(
    r"^\s*(?:-\s*)?uses:\s*(?P<target>[^\s#]+)(?:\s+#\s*(?P<tag>\S+))?\s*$",
    re.MULTILINE,
)
# Release tags were resolved and peeled to commits through the GitHub API on
# 2026-10-29. The selected runtimes all use Node 24; keeping the exact release
# tag in a comment makes the otherwise opaque commit reviewable.
ACTION_PINS: dict[str, tuple[str, str]] = {
    "actions/checkout": ("d23441a48e516b6c34aea4fa41551a30e30af803", "v6.1.0"),
    "actions/setup-python": ("ece7cb06caefa5fff74198d8649806c4678c61a1", "v6.3.0"),
    "actions/setup-node": ("249970729cb0ef3589644e2896645e5dc5ba9c38", "v6.5.0"),
    "actions/upload-artifact": ("cf430e030ddbb5b0abf93d22962f4752f3646cd9", "v7.0.2"),
    "actions/download-artifact": ("9000827ccba6bdab643e8b6fd33ac0654aef8333", "v8.0.2"),
    "actions/github-script": ("3a2844b7e9c422d3c10d287c895573f7108da1b3", "v9.0.0"),
    "gitleaks/gitleaks-action": ("e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e", "v3.0.0"),
    "github/codeql-action": ("9b3254085494b55a0f7c4c2b3d2d52cc712ce370", "v4.39.1"),
}


def _workflow_paths() -> list[Path]:
    return sorted(
        [*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")],
        key=lambda path: path.as_posix(),
    )


def test_external_actions_are_pinned_to_reviewed_release_commits() -> None:
    failures: list[str] = []
    seen_repositories: set[str] = set()
    for path in _workflow_paths():
        source = path.read_text(encoding="utf-8")
        for match in USES_RE.finditer(source):
            target = match.group("target")
            if target.startswith(("./", "../")):
                continue
            action_path = target.split("@", 1)[0]
            repository = "/".join(action_path.split("/")[:2])
            seen_repositories.add(repository)
            expected = ACTION_PINS.get(repository)
            if expected is None:
                failures.append(f"{path.relative_to(ROOT)}: unreviewed external action {target}")
                continue
            sha, tag = expected
            if target != f"{action_path}@{sha}" or match.group("tag") != tag:
                failures.append(f"{path.relative_to(ROOT)}: {target} must use reviewed {repository}@{sha} # {tag}")
    assert seen_repositories == set(ACTION_PINS)
    assert not failures, "Unpinned or unreviewed GitHub Actions:\n" + "\n".join(failures)


def test_hosted_runner_labels_do_not_follow_mutable_latest_aliases() -> None:
    failures = [
        str(path.relative_to(ROOT))
        for path in _workflow_paths()
        if re.search(r"(?<![\w-])(?:ubuntu|windows|macos)-latest(?![\w-])", path.read_text(encoding="utf-8"))
    ]
    assert not failures, "Mutable runner aliases: " + ", ".join(failures)


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


def test_every_github_actions_workflow_declares_token_permissions() -> None:
    """GITHUB_TOKEN access is explicit instead of inheriting repository defaults."""

    failures: list[str] = []
    for path in _workflow_paths():
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        if not isinstance(document, dict) or not isinstance(document.get("permissions"), dict):
            failures.append(f"{path.relative_to(ROOT)}: missing top-level permissions mapping")

    assert not failures, "Workflows with implicit GITHUB_TOKEN permissions:\n" + "\n".join(failures)


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
                failures.append(f"{relative_workflow}: missing referenced Python path {reference}")

    assert not failures, "GitHub Actions workflows reference missing Python paths:\n" + "\n".join(failures)
