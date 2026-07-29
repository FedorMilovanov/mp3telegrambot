from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.voxcpm2.examples.john_piper_z20py4yqhyq import master_constant_mix as master


def test_master_probe_rejects_nan_duration(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        master,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="nan", stderr=""),
    )
    with pytest.raises(RuntimeError, match="Нефинитное значение"):
        master.probe_duration(tmp_path / "source.mp4")


def test_master_probe_rejects_zero_duration(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        master,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="0", stderr=""),
    )
    with pytest.raises(RuntimeError, match="Некорректная длительность"):
        master.probe_duration(tmp_path / "source.mp4")


def test_master_loudnorm_parser_uses_last_valid_object() -> None:
    text = (
        "noise {not json}\n"
        '{"input_i":"-20","input_tp":"-2","input_lra":"3",'
        '"input_thresh":"-30","target_offset":"1"}\n'
        "more noise\n"
        '{"input_i":"-16.1","input_tp":"-1.2","input_lra":"4",'
        '"input_thresh":"-26","target_offset":"0.1"}\n'
    )
    result = master.parse_loudnorm(text)
    assert result["input_i"] == "-16.1"
    assert result["target_offset"] == "0.1"


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_master_loudnorm_parser_rejects_nonfinite_measurement(value: str) -> None:
    text = (
        '{"input_i":"' + value + '","input_tp":"-1.2",'
        '"input_lra":"4","input_thresh":"-26","target_offset":"0.1"}'
    )
    with pytest.raises(RuntimeError, match="Нефинитное значение"):
        master.parse_loudnorm(text)


def test_two_pass_master_rejects_invalid_targets_before_ffmpeg(
    monkeypatch,
    tmp_path,
) -> None:
    called = False

    def run_should_not_happen(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("FFmpeg must not run")

    monkeypatch.setattr(master, "run", run_should_not_happen)
    with pytest.raises(RuntimeError, match="target_i"):
        master.two_pass_master(
            tmp_path / "input.wav",
            tmp_path / "output.wav",
            target_i=float("nan"),
            target_lra=8.0,
            target_tp=-1.5,
        )
    assert called is False


def test_constant_mix_keeps_original_branch_exact_and_has_no_post_mix_gain(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[list[str]] = []

    def capture(command: list[str], **_kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(master, "run", capture)
    graph = master.build_constant_mix(
        source=tmp_path / "source.mp4",
        mastered_russian=tmp_path / "russian.wav",
        output=tmp_path / "mix.wav",
        source_duration=59.0,
        original_level=0.18,
        russian_gain=0.75,
    )

    assert calls
    assert "volume=0.180000000[original]" in graph
    assert "volume=0.750000000[russian]" in graph
    after_sum = graph.split("amix=", 1)[1]
    assert "loudnorm" not in after_sum
    assert "alimiter" not in after_sum
    assert "volume=" not in after_sum


def test_calibration_changes_only_russian_gain(monkeypatch, tmp_path) -> None:
    gains: list[tuple[float, float]] = []

    def fake_mix(**kwargs):
        gains.append((float(kwargs["original_level"]), float(kwargs["russian_gain"])))
        return "fixed graph"

    measurements = iter(
        [
            {
                "integrated_lufs": -16.0,
                "true_peak_dbtp": -3.0,
                "lra_lu": 4.0,
                "threshold_lufs": -26.0,
            }
        ]
        * 12
    )
    monkeypatch.setattr(master, "build_constant_mix", fake_mix)
    monkeypatch.setattr(master, "measure_loudness", lambda *_args, **_kwargs: next(measurements))

    report = master.calibrate_russian_gain(
        source=tmp_path / "source.mp4",
        mastered_russian=tmp_path / "russian.wav",
        output=tmp_path / "mix.wav",
        work_dir=tmp_path,
        source_duration=59.0,
        original_level=0.18,
        target_i=-16.0,
        target_lra=8.0,
        target_tp=-1.5,
    )

    assert gains
    assert {original for original, _gain in gains} == {0.18}
    assert report["original_gain_changes_after_sum"] is False
    assert report["post_mix_loudnorm"] is False
    assert report["post_mix_limiter"] is False
