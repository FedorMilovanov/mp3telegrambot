from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import mp3_conversion


def _write_bytes(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)


def _audio_file_is_nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 1024


def _glob_paths(directory: Path, pattern: str) -> list[Path]:
    return list(directory.glob(pattern))


@pytest.mark.asyncio
async def test_atomic_mp3_conversion_publishes_only_after_probe(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp3"
    output = tmp_path / "out.mp3"
    source.write_bytes(b"source" * 4096)
    monkeypatch.setattr(mp3_conversion.shutil, "which", lambda name: f"/{name}")

    async def fake_run(command, **_kwargs):
        await asyncio.to_thread(_write_bytes, Path(command[-1]), b"encoded" * 4096)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    async def fake_probe(path):
        return await asyncio.to_thread(_audio_file_is_nonempty, Path(path))

    monkeypatch.setattr(mp3_conversion, "run_cancellable_process", fake_run)
    monkeypatch.setattr(mp3_conversion, "_probe_audio_file", fake_probe)
    assert await mp3_conversion.reencode_mp3_64k_atomic(source, output) is True
    assert output.is_file()
    temporary_parts = await asyncio.to_thread(_glob_paths, tmp_path, "*.part-*.mp3")
    assert not temporary_parts


@pytest.mark.asyncio
async def test_atomic_mp3_conversion_reuses_probed_current_output(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp3"
    output = tmp_path / "out.mp3"
    source.write_bytes(b"source" * 4096)
    output.write_bytes(b"encoded" * 4096)
    monkeypatch.setattr(mp3_conversion.shutil, "which", lambda name: f"/{name}")
    probes = []

    async def fake_probe(path):
        probes.append(Path(path))
        return True

    async def unexpected_run(_command, **_kwargs):
        pytest.fail("current proved output must not be encoded again")

    monkeypatch.setattr(mp3_conversion, "_probe_audio_file", fake_probe)
    monkeypatch.setattr(mp3_conversion, "run_cancellable_process", unexpected_run)

    assert await mp3_conversion.reencode_mp3_64k_atomic(source, output) is True
    assert probes == [output]


@pytest.mark.asyncio
async def test_atomic_mp3_conversion_rejects_same_path(tmp_path: Path) -> None:
    source = tmp_path / "source.mp3"
    source.write_bytes(b"source")

    with pytest.raises(ValueError, match="paths must differ"):
        await mp3_conversion.reencode_mp3_atomic(source, source)


@pytest.mark.asyncio
async def test_atomic_mp3_conversion_chains_resolve_failure_for_same_path(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp3"
    source.write_bytes(b"source")

    def denied_resolve(_self, strict=False):
        raise OSError("resolve denied")

    monkeypatch.setattr(Path, "resolve", denied_resolve)
    with pytest.raises(ValueError, match="paths must differ") as caught:
        await mp3_conversion.reencode_mp3_atomic(source, source)
    assert isinstance(caught.value.__cause__, OSError)


@pytest.mark.asyncio
async def test_atomic_mp3_conversion_keeps_existing_output_on_failed_probe(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp3"
    output = tmp_path / "out.mp3"
    source.write_bytes(b"source" * 4096)
    output.write_bytes(b"old" * 4096)
    old = output.read_bytes()
    monkeypatch.setattr(mp3_conversion.shutil, "which", lambda name: f"/{name}")

    async def fake_run(command, **_kwargs):
        await asyncio.to_thread(_write_bytes, Path(command[-1]), b"bad" * 4096)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    async def fake_probe(_path):
        return False

    monkeypatch.setattr(mp3_conversion, "run_cancellable_process", fake_run)
    monkeypatch.setattr(mp3_conversion, "_probe_audio_file", fake_probe)
    source.touch()
    assert await mp3_conversion.reencode_mp3_64k_atomic(source, output) is False
    assert output.read_bytes() == old
