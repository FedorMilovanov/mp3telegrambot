from pathlib import Path

import pytest

from scripts.required_pr_gate import (
    PROFILE_WORKFLOWS,
    expected_profile_workflows,
    extract_pull_request_filter,
    github_path_matches,
    workflow_applies,
)


ROOT = Path(__file__).resolve().parents[1]


def test_github_path_match_supports_repository_workflow_patterns():
    assert github_path_matches("bot.py", "*.py")
    assert not github_path_matches("services/bot.py", "*.py")
    assert github_path_matches("bot.py", "**/*.py")
    assert github_path_matches("services/bot.py", "**/*.py")
    assert github_path_matches("handlers/dub_runtime/x.py", "handlers/dub_*/**/*.py")
    assert github_path_matches("Start Bot.bat", "Start Bot.bat")
    assert not github_path_matches("README.md", "**/*.py")


def test_extract_pull_request_filter_handles_paths_and_paths_ignore():
    paths_mode, paths = extract_pull_request_filter(
        """on:\n  pull_request:\n    branches: [\"main\"]\n    paths:\n      - \"**/*.py\"\n      - '.env.example'\n"""
    )
    assert paths_mode == "paths"
    assert paths == ("**/*.py", ".env.example")

    ignore_mode, ignored = extract_pull_request_filter(
        """on:\n  pull_request:\n    paths-ignore:\n      - \"**/*.md\"\n      - .gitignore\n"""
    )
    assert ignore_mode == "paths-ignore"
    assert ignored == ("**/*.md", ".gitignore")


def test_workflow_applies_matches_github_trigger_semantics():
    assert workflow_applies("paths", ("**/*.py",), ("services/x.py",))
    assert not workflow_applies("paths", ("**/*.py",), ("README.md",))
    assert workflow_applies(
        "paths-ignore", ("**/*.md", ".gitignore"), ("README.md", "bot.py")
    )
    assert not workflow_applies(
        "paths-ignore", ("**/*.md", ".gitignore"), ("README.md", ".gitignore")
    )
    with pytest.raises(ValueError, match="negative patterns"):
        workflow_applies("paths-ignore", ("!README.md",), ("README.md",))


def test_required_profile_workflow_filters_are_parseable_and_supported():
    for workflow in PROFILE_WORKFLOWS:
        path = ROOT / workflow
        assert path.is_file(), workflow
        mode, patterns = extract_pull_request_filter(path.read_text(encoding="utf-8"))
        assert mode in {"all", "paths", "paths-ignore"}
        if mode != "all":
            assert patterns
        for pattern in patterns:
            github_path_matches("sentinel/path.py", pattern.lstrip("!"))


def test_expected_profile_workflows_use_live_workflow_filters():
    assert set(expected_profile_workflows(ROOT, ("README.md",))) == set()

    assert set(
        expected_profile_workflows(ROOT, ("services/youtube_po_token_runtime.py",))
    ) == {
        ".github/workflows/cut-policy-ci.yml",
        ".github/workflows/gemini-qa-policy.yml",
        ".github/workflows/windows-bootstrap-ci.yml",
    }

    assert set(expected_profile_workflows(ROOT, ("services/dub_example.py",))) == {
        ".github/workflows/gemini-qa-policy.yml",
        ".github/workflows/dub-studio-checks.yml",
    }

    assert set(expected_profile_workflows(ROOT, ("requirements-lock.txt",))) == {
        ".github/workflows/cut-policy-ci.yml",
        ".github/workflows/windows-bootstrap-ci.yml",
        ".github/workflows/dub-studio-checks.yml",
    }

    assert set(expected_profile_workflows(ROOT, (".env.example",))) == {
        ".github/workflows/gemini-qa-policy.yml"
    }
