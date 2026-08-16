from __future__ import annotations

from pathlib import Path

from services.speech_backends import get_backend
from tools.voxcpm2 import master_direct_russian_only as master


def test_russian_only_mix_never_uses_source_audio(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(master.base, "run", lambda command: commands.append(list(command)))
    source = tmp_path / "source.mp4"
    russian = tmp_path / "russian.wav"
    output = tmp_path / "mix.wav"
    graph = master.build_russian_only_mix(
        source=source,
        mastered_russian=russian,
        output=output,
        source_duration=12.5,
        original_level=0.18,
        russian_gain=1.0,
    )
    assert commands
    command = commands[0]
    assert str(russian) in command
    assert str(source) not in command
    assert "-af" in command
    assert "volume=1.000000000" in graph
    assert master.POLICY.startswith("russian-only-direct-master")


def test_backend_points_direct_mode_to_real_owner() -> None:
    backend = get_backend("voxcpm2")
    repo = Path(__file__).resolve().parents[1]
    path, module = backend._master_contract(repo, {"translation_mode": "direct"}) if hasattr(backend, "_master_contract") else (None, None)
    if path is None:
        from services.speech_backends import voxcpm2
        path, module = voxcpm2._master_contract(repo, {"translation_mode": "direct"})
    assert path.name == "master_direct_russian_only.py"
    assert module == "tools.voxcpm2.master_direct_russian_only"


def test_direct_master_has_no_runtime_surgery() -> None:
    source = Path(master.__file__).read_text(encoding="utf-8")
    assert "def install(" not in source
    assert "sys.modules" not in source
    assert "setattr(" not in source
    assert "master_monolithic_mix" not in source
