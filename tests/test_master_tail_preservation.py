from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MASTER = (
    ROOT
    / "tools"
    / "voxcpm2"
    / "examples"
    / "john_piper_z20py4yqhyq"
    / "master_constant_mix.py"
)


def test_master_cannot_end_with_short_source_audio() -> None:
    source = MASTER.read_text(encoding="utf-8")
    ast.parse(source)
    assert "amix=inputs=2:duration=longest" in source
    assert "amix=inputs=2:duration=first" not in source
    assert "[0:a]asetpts=PTS-STARTPTS" in source
    assert "[1:a]asetpts=PTS-STARTPTS" in source
    assert "atrim=duration={source_duration:.6f}" in source
    assert '"mix_duration_policy": "longest-then-exact-video-trim"' in source
