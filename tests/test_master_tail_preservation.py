from __future__ import annotations

from pathlib import Path

from tools.voxcpm2.examples.john_piper_z20py4yqhyq import (
    master_constant_mix as master,
)


def test_master_cannot_end_with_short_source_audio(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        master,
        "run",
        lambda command, **_kwargs: commands.append(list(command)),
    )

    graph = master.build_constant_mix(
        source=tmp_path / "source.mp4",
        mastered_russian=tmp_path / "russian.wav",
        output=tmp_path / "mixed.wav",
        source_duration=12.5,
        original_level=0.18,
        russian_gain=0.95,
    )

    assert "amix=inputs=2:duration=longest" in graph
    assert "amix=inputs=2:duration=first" not in graph
    assert "[0:a]asetpts=PTS-STARTPTS" in graph
    assert "[1:a]asetpts=PTS-STARTPTS" in graph
    assert "apad=pad_dur=12.500000" in graph
    assert "atrim=duration=12.500000" in graph
    assert commands
    assert commands[0][commands[0].index("-filter_complex") + 1] == graph
    assert commands[0][-1].endswith("mixed.wav")
