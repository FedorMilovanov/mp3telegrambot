from __future__ import annotations

from pathlib import Path
import sys

from services.tts_weighted_smoke_runner import WeightedTTSSmokeRunnerConfig
from tools.check_tts_weighted_smoke_runner import _redacted_failure


def test_runner_doctor_failure_redacts_every_local_path(tmp_path: Path) -> None:
    model = tmp_path / "private-model"
    reference = tmp_path / "private-reference.wav"
    work = tmp_path / "private-doctor-work"
    expected = tmp_path / "private-python.exe"
    config = WeightedTTSSmokeRunnerConfig(
        profile_id="voxcpm2-production-v1",
        model_root=model,
        reference_wav=reference,
        work_dir=work,
        expected_python=expected,
    )
    repository = Path.cwd().resolve()
    message = (
        f"model={model}; reference={reference}; work={work}; "
        f"expected={expected}; current={Path(sys.executable).resolve()}; "
        f"repo={repository}"
    )

    redacted = _redacted_failure(RuntimeError(message), config)

    for private in (
        model,
        reference,
        work,
        expected,
        Path(sys.executable).resolve(),
        repository,
    ):
        assert str(private) not in redacted
        assert str(private).replace("\\", "/") not in redacted
    assert "<MODEL_ROOT>" in redacted
    assert "<REFERENCE_WAV>" in redacted
    assert "<WORK_DIR>" in redacted
    assert "<EXPECTED_PYTHON>" in redacted
    assert "<CURRENT_PYTHON>" in redacted
    assert "<REPOSITORY>" in redacted
