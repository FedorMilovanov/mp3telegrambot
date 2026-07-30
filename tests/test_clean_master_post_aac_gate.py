from __future__ import annotations

from types import SimpleNamespace

from tools.voxcpm2 import clean_production_core as core


def _master_command() -> list[str]:
    return [
        "python.exe",
        r"C:\repo\tools\voxcpm2\examples\master_constant_mix.py",
        "--work-dir",
        r"C:\project\master_work",
        "--russian-only-video",
        r"C:\project\output\russian_only.mp4",
    ]


def test_successful_master_must_pass_post_aac_delivery_gate(monkeypatch) -> None:
    calls: list[list[str]] = []
    verified: list[list[str]] = []

    def fake_run(command, *args, **kwargs):
        calls.append(list(command))
        return SimpleNamespace(returncode=0, stderr="")

    def fake_verify(command):
        verified.append(list(command))
        return {"passed": True}

    monkeypatch.setattr(core._stdlib_subprocess, "run", fake_run)
    monkeypatch.setattr(core, "_verify_post_aac_master_output", fake_verify)

    result = core._run_child_process(_master_command())

    assert result.returncode == 0
    assert calls == [_master_command()]
    assert verified == [_master_command()]


def test_post_aac_failure_is_not_returned_as_success(monkeypatch) -> None:
    def fake_run(command, *args, **kwargs):
        return SimpleNamespace(returncode=0, stderr="")

    def fake_verify(command):
        raise RuntimeError("late_broadband_burst")

    monkeypatch.setattr(core._stdlib_subprocess, "run", fake_run)
    monkeypatch.setattr(core, "_verify_post_aac_master_output", fake_verify)

    try:
        core._run_child_process(_master_command())
    except RuntimeError as exc:
        assert "post-AAC ending/tail QA" in str(exc)
        assert "late_broadband_burst" in str(exc)
    else:
        raise AssertionError("Post-AAC failure was incorrectly released")
