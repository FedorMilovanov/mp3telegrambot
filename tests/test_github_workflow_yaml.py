from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def test_all_github_workflows_are_valid_yaml() -> None:
    failures: list[str] = []
    workflow_paths = sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")))
    assert workflow_paths, "No GitHub workflow files were found."

    for path in workflow_paths:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            failures.append(f"{path.relative_to(ROOT)}: {exc}")
            continue
        if not isinstance(document, dict):
            failures.append(
                f"{path.relative_to(ROOT)}: top-level YAML document must be a mapping"
            )

    assert not failures, "Invalid GitHub workflow YAML:\n" + "\n".join(failures)


def test_john_piper_here_string_remains_inside_the_run_block() -> None:
    path = WORKFLOWS / "john-piper-parser.yml"
    source = path.read_text(encoding="utf-8")
    assert "          import json\n" in source
    assert "          '@ | python - $segments\n" in source
    assert "\nimport json\n" not in source
