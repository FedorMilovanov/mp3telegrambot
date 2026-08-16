from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from services import mp3_conversion


@pytest.mark.asyncio
async def test_atomic_mp3_conversion_publishes_only_after_probe(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp3"
    output = tmp_path / "out.mp3"
    source.write_bytes(b"source" * 4096)
    monkeypatch.setattr(mp3_conversion.shutil, "which", lambda name: f"/{name}")
    async def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"encoded" * 4096)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    async def fake_probe(path):
        return Path(path).is_file() and Path(path).stat().st_size > 1024
    monkeypatch.setattr(mp3_conversion, "run_cancellable_process", fake_run)
    monkeypatch.setattr(mp3_conversion, "_probe_audio_file", fake_probe)
    assert await mp3_conversion.reencode_mp3_64k_atomic(source, output) is True
    assert output.is_file()
    assert not list(tmp_path.glob("*.part-*.mp3"))


@pytest.mark.asyncio
async def test_atomic_mp3_conversion_keeps_existing_output_on_failed_probe(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp3"
    output = tmp_path / "out.mp3"
    source.write_bytes(b"source" * 4096)
    output.write_bytes(b"old" * 4096)
    old = output.read_bytes()
    monkeypatch.setattr(mp3_conversion.shutil, "which", lambda name: f"/{name}")
    async def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"bad" * 4096)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    async def fake_probe(_path):
        return False
    monkeypatch.setattr(mp3_conversion, "run_cancellable_process", fake_run)
    monkeypatch.setattr(mp3_conversion, "_probe_audio_file", fake_probe)
    source.touch()
    assert await mp3_conversion.reencode_mp3_64k_atomic(source, output) is False
    assert output.read_bytes() == old
