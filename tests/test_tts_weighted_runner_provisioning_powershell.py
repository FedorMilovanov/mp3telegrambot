from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "voxcpm2" / "Prepare-TTSWeightsRunner.ps1"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_powershell_defaults_to_read_only_validation() -> None:
    source = _source()
    assert "[ValidateSet('Validate', 'Apply')]" in source
    assert "[string]$Mode = 'Validate'" in source
    assert "if ($Mode -eq 'Apply')" in source
    assert "Test-IsAdministrator" in source
    assert "$PSCmdlet.ShouldProcess" in source
    assert "Restart-Service -Name $ServiceName" in source
    assert "WaitForStatus" in source


def test_powershell_persists_only_required_machine_bindings() -> None:
    source = _source()
    for key in (
        "TTS_SMOKE_PYTHON",
        "TTS_SMOKE_MODEL_ROOT",
        "TTS_SMOKE_REFERENCE_WAV",
    ):
        assert source.count(key) == 1
    assert "[EnvironmentVariableTarget]::Machine" in source
    assert "[EnvironmentVariableTarget]::Process" in source
    assert "Repository" not in source.split("$Bindings =", 1)[1].split("}", 1)[0]
    assert "ProfileId" not in source.split("$Bindings =", 1)[1].split("}", 1)[0]


def test_powershell_never_handles_registration_credentials_or_labels() -> None:
    source = _source().casefold()
    forbidden = (
        "runner_token",
        "registration-token",
        "--token",
        "--pat",
        "github_token",
        "secrets.",
        "--labels",
        "actions/runners/registration-token",
    )
    for marker in forbidden:
        assert marker not in source


def test_powershell_calls_the_repository_checker_with_literal_arguments() -> None:
    source = _source()
    assert "tools\\check_tts_weighted_runner_provisioning.py" in source
    assert "--runner-directory $ResolvedRunner" in source
    assert "--repository $Repository" in source
    assert "--profile-id $ProfileId" in source
    assert "--python-executable $ResolvedPython" in source
    assert "--model-directory $ResolvedModel" in source
    assert "--reference-wav $ResolvedReference" in source
    assert "--work-directory $WorkDirectory" in source
    assert "if ($LASTEXITCODE -ne 0)" in source


def test_powershell_failure_output_does_not_echo_exception_or_paths() -> None:
    source = _source()
    catch_block = source.split("catch {", 1)[1]
    assert "$_.Exception.GetType().Name" in catch_block
    assert "Write-Error \"TTS_WEIGHTS_RUNNER_SETUP_FAILED $FailureType\"" in catch_block
    assert "$_.Exception.Message" not in catch_block
    assert "Write-Host $Resolved" not in source
    assert "Write-Output $Resolved" not in source


def test_powershell_requires_service_descriptor_and_running_service() -> None:
    source = _source()
    assert "Get-RunnerServiceName" in source
    assert "Get-Service -Name $ServiceName" in source
    assert "actions.runner." in source
    assert ".service" in source
    assert "ServiceControllerStatus]::Running" in source
