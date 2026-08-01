from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tts-weighted-smoke.yml"
CONTRACT_WORKFLOW = (
    ROOT / ".github" / "workflows" / "tts-weighted-smoke-contract.yml"
)


def _load(path: Path) -> dict:
    payload = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    return payload


def _workflow() -> dict:
    return _load(WORKFLOW)


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
    assert run_blocks.count("--profile-id $env:TTS_SMOKE_PROFILE_ID_INPUT") == 2
    assert "--duration-budget $env:TTS_SMOKE_DURATION_INPUT" in run_blocks
    assert "TTS_SMOKE_PROFILE_ID_INPUT -notmatch" in run_blocks
    assert "TryParse" in run_blocks


def test_attestation_is_a_hard_precondition_for_summary_and_cleanup() -> None:
    payload = _workflow()
    steps = payload["jobs"]["weighted-smoke"]["steps"]
    names = [str(step.get("name") or "") for step in steps]

    doctor_index = names.index("Run trusted runner doctor")
    synthesis_index = names.index("Run real weighted synthesis")
    attestation_index = names.index("Build privacy-safe attestation")
    upload_index = names.index("Upload weighted smoke attestation")
    summary_index = names.index("Publish sanitized summary")
    cleanup_index = names.index("Remove smoke data")
    assert (
        doctor_index
        < synthesis_index
        < attestation_index
        < upload_index
        < summary_index
        < cleanup_index
    )

    doctor = steps[doctor_index]
    synthesis = steps[synthesis_index]
    attestation = steps[attestation_index]
    summary = steps[summary_index]
    assert "tools/check_tts_weighted_smoke_runner.py" in doctor["run"]
    assert "--expected-python $env:TTS_SMOKE_PYTHON" in doctor["run"]
    assert "TTS_SMOKE_DOCTOR_WORK=$DoctorWork" in doctor["run"]
    assert "Runner doctor did not publish its work root" in synthesis["run"]
    assert "tools/build_tts_weighted_smoke_attestation.py" in attestation["run"]
    assert "--doctor-report $DoctorPath" in attestation["run"]
    assert "--smoke-report $ReportPath" in attestation["run"]
    assert "TTS_SMOKE_ATTESTATION=$AttestationPath" in attestation["run"]
    assert "Sanitized weighted smoke attestation is missing" in summary["run"]
    assert "$Attestation.subject.commit_sha -ne $env:GITHUB_SHA" in summary["run"]
    assert "$Attestation.digest_sha256" in summary["run"]


def test_workflow_uploads_only_one_fixed_privacy_safe_attestation() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    payload = _workflow()
    steps = payload["jobs"]["weighted-smoke"]["steps"]
    actions = [str(step.get("uses") or "") for step in steps if step.get("uses")]

    assert actions == ["actions/checkout@v4", "actions/upload-artifact@v4"]
    upload = next(
        step for step in steps if step.get("name") == "Upload weighted smoke attestation"
    )
    settings = upload["with"]
    assert settings["name"] == (
        "tts-weighted-smoke-attestation-${{ github.run_id }}-"
        "${{ github.run_attempt }}"
    )
    assert settings["path"] == "tts-weighted-smoke-attestation/attestation.json"
    assert settings["if-no-files-found"] == "error"
    assert settings["retention-days"] == "90"
    assert settings["compression-level"] == "0"
    assert settings["overwrite"] == "false"
    assert settings["include-hidden-files"] == "false"
    assert "*" not in settings["path"]
    assert "report.json" not in settings["path"]
    assert "doctor" not in settings["path"]
    assert "smoke/" not in settings["path"]
    assert "weighted-smoke.wav" not in source
    assert "execution-plan.jsonl" not in source

    cleanup = next(step for step in steps if step.get("name") == "Remove smoke data")
    assert cleanup["if"] == "always()"
    assert "TTS_SMOKE_ROOT_WORK" in cleanup["run"]
    assert "tts-weighted-smoke-attestation" in cleanup["run"]
    checkout = next(step for step in steps if step.get("uses") == "actions/checkout@v4")
    assert checkout["with"]["persist-credentials"] == "false"
    assert checkout["with"]["ref"] == "${{ github.sha }}"


def test_workflow_uses_runner_and_github_environment_not_repository_secrets() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in source
    assert "$env:TTS_SMOKE_PYTHON" in source
    assert "$env:TTS_SMOKE_MODEL_ROOT" in source
    assert "$env:TTS_SMOKE_REFERENCE_WAV" in source
    assert "$env:GITHUB_SHA" in source
    assert "Get-Content -LiteralPath $DoctorPath" in source
    assert "Get-Content -LiteralPath $ReportPath" in source
    assert "Get-Content -LiteralPath $env:TTS_SMOKE_ATTESTATION" in source
    assert "audio_retained" in source


def test_trusted_python_compiles_doctor_smoke_and_attestation_surfaces() -> None:
    payload = _workflow()
    step = next(
        item
        for item in payload["jobs"]["weighted-smoke"]["steps"]
        if item.get("name") == "Validate repository contracts with trusted Python"
    )
    run = str(step["run"])
    assert "services/tts_weighted_smoke_runner.py" in run
    assert "tools/check_tts_weighted_smoke_runner.py" in run
    assert "services/tts_weighted_smoke.py" in run
    assert "tools/run_tts_weighted_smoke.py" in run
    assert "services/tts_weighted_smoke_attestation.py" in run
    assert "tools/build_tts_weighted_smoke_attestation.py" in run


def test_contract_workflow_uses_only_workflow_safe_concurrency_context() -> None:
    payload = _load(CONTRACT_WORKFLOW)
    concurrency = payload["concurrency"]
    group = str(concurrency["group"])

    assert group == "tts-weighted-smoke-contract-${{ github.ref }}"
    assert "matrix." not in group
    job = payload["jobs"]["contract"]
    assert job["strategy"]["matrix"]["os"] == [
        "ubuntu-latest",
        "windows-latest",
    ]
    assert job["runs-on"] == "${{ matrix.os }}"
