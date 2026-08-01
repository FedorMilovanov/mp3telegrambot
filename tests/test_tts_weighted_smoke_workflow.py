from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tts-weighted-smoke.yml"


def _workflow() -> dict:
    payload = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    return payload


def test_weighted_smoke_is_manual_main_only_and_least_privilege() -> None:
    payload = _workflow()
    triggers = payload["on"]
    assert set(triggers) == {"workflow_dispatch"}
    assert "push" not in triggers
    assert "pull_request" not in triggers
    assert "pull_request_target" not in triggers
    assert "schedule" not in triggers
    assert payload["permissions"] == {"contents": "read"}

    job = payload["jobs"]["weighted-smoke"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["runs-on"] == ["self-hosted", "Windows", "X64", "tts-weights"]
    assert job["timeout-minutes"] == "90"
    assert job["env"]["TTS_SMOKE_PROFILE_ID_INPUT"] == "${{ inputs.profile_id }}"
    assert job["env"]["TTS_SMOKE_DURATION_INPUT"] == "${{ inputs.duration_budget }}"


def test_dispatch_inputs_are_never_interpolated_into_shell_scripts() -> None:
    payload = _workflow()
    steps = payload["jobs"]["weighted-smoke"]["steps"]
    run_blocks = "\n".join(str(step.get("run") or "") for step in steps)

    assert "${{ inputs.profile_id }}" not in run_blocks
    assert "${{ inputs.duration_budget }}" not in run_blocks
    assert "--profile-id $env:TTS_SMOKE_PROFILE_ID_INPUT" in run_blocks
    assert "--duration-budget $env:TTS_SMOKE_DURATION_INPUT" in run_blocks
    assert "TTS_SMOKE_PROFILE_ID_INPUT -notmatch" in run_blocks
    assert "TryParse" in run_blocks


def test_workflow_never_uploads_voice_or_report_artifacts() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    payload = _workflow()
    steps = payload["jobs"]["weighted-smoke"]["steps"]
    actions = [str(step.get("uses") or "") for step in steps if step.get("uses")]

    assert actions == ["actions/checkout@v4"]
    assert "upload-artifact" not in source
    assert "artifact" not in source.casefold()
    assert "Remove smoke data" in [step.get("name") for step in steps]
    cleanup = next(step for step in steps if step.get("name") == "Remove smoke data")
    assert cleanup["if"] == "always()"
    checkout = next(step for step in steps if step.get("uses") == "actions/checkout@v4")
    assert checkout["with"]["persist-credentials"] == "false"
    assert checkout["with"]["ref"] == "${{ github.sha }}"


def test_workflow_uses_runner_environment_not_repository_secrets() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in source
    assert "$env:TTS_SMOKE_PYTHON" in source
    assert "$env:TTS_SMOKE_MODEL_ROOT" in source
    assert "$env:TTS_SMOKE_REFERENCE_WAV" in source
    assert "Get-Content -LiteralPath $ReportPath" in source
    assert "audio_retained" in source
