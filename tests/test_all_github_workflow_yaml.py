from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"


def test_every_github_actions_workflow_is_valid_yaml() -> None:
    workflow_paths = sorted(
        [*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")],
        key=lambda path: path.as_posix(),
    )
    assert workflow_paths, "No GitHub Actions workflow files were found."

    failures: list[str] = []
    for path in workflow_paths:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (UnicodeError, OSError, yaml.YAMLError) as exc:
            failures.append(f"{path.relative_to(ROOT)}: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(document, dict):
            failures.append(
                f"{path.relative_to(ROOT)}: top-level YAML document must be a mapping"
            )

    assert not failures, "Invalid GitHub Actions workflow YAML:\n" + "\n".join(failures)
