from types import SimpleNamespace

import pytest

import services.shorts_factory_source as source


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("60", 1800),
        ("1800", 1800),
        ("2400", 2400),
        ("99999", 7200),
        ("broken", 1800),
    ],
)
def test_factory_livedub_timeout_has_quality_floor_and_safe_cap(
    monkeypatch,
    raw,
    expected,
):
    monkeypatch.setenv("SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC", raw)
    assert source._factory_livedub_timeout_seconds() == expected


@pytest.mark.asyncio
async def test_failed_native_audio_download_cleans_all_factory_artifacts(
    monkeypatch,
    tmp_path,
):
    raw_path = tmp_path / "video_factory_audio_source.webm"
    partial_path = tmp_path / "video_factory_audio_source.webm.part"

    async def fake_run(command, **kwargs):
        raw_path.write_bytes(b"raw")
        partial_path.write_bytes(b"partial")
        return SimpleNamespace(returncode=1, stderr="download failed")

    monkeypatch.setattr(source, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(source, "run_cancellable_process", fake_run)

    with pytest.raises(RuntimeError, match="audio download failed"):
        await source.download_factory_audio_source(
            "https://youtu.be/example",
            "video",
        )

    assert not list(tmp_path.glob("video_factory_audio_*"))


@pytest.mark.asyncio
async def test_cancelled_native_audio_download_also_cleans_partials(
    monkeypatch,
    tmp_path,
):
    partial_path = tmp_path / "video_factory_audio_source.webm.part"

    async def fake_run(command, **kwargs):
        partial_path.write_bytes(b"partial")
        raise __import__("asyncio").CancelledError

    monkeypatch.setattr(source, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(source, "run_cancellable_process", fake_run)

    with pytest.raises(__import__("asyncio").CancelledError):
        await source.download_factory_audio_source(
            "https://youtu.be/example",
            "video",
        )

    assert not list(tmp_path.glob("video_factory_audio_*"))


@pytest.mark.asyncio
async def test_failed_video_download_cleans_all_factory_artifacts(
    monkeypatch,
    tmp_path,
):
    raw_path = tmp_path / "video_factory_max_source.mkv"
    partial_path = tmp_path / "video_factory_max_source.webm.part"

    async def fake_run(command, **kwargs):
        raw_path.write_bytes(b"raw")
        partial_path.write_bytes(b"partial")
        return SimpleNamespace(returncode=1, stderr="download failed")

    monkeypatch.setattr(source, "run_cancellable_process", fake_run)

    with pytest.raises(RuntimeError, match="video download failed"):
        await source.download_factory_video_source(
            "https://youtu.be/example",
            "video",
            workdir=tmp_path,
        )

    assert not list(tmp_path.glob("video_factory_max_source.*"))


@pytest.mark.asyncio
async def test_cancelled_video_download_also_cleans_partials(
    monkeypatch,
    tmp_path,
):
    partial_path = tmp_path / "video_factory_max_source.webm.part"

    async def fake_run(command, **kwargs):
        partial_path.write_bytes(b"partial")
        raise __import__("asyncio").CancelledError

    monkeypatch.setattr(source, "run_cancellable_process", fake_run)

    with pytest.raises(__import__("asyncio").CancelledError):
        await source.download_factory_video_source(
            "https://youtu.be/example",
            "video",
            workdir=tmp_path,
        )

    assert not list(tmp_path.glob("video_factory_max_source.*"))
