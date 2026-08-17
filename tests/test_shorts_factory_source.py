from pathlib import Path
from types import SimpleNamespace

import pytest

import services.livedub_mix as livedub_mix
import services.shorts_factory_candidates as candidates
import services.shorts_factory_capacity as capacity
import services.shorts_factory_source as source
import services.yandex_live_dub as yandex_live_dub


def _audio_probe(codec="opus", duration=120.0):
    return SimpleNamespace(
        duration=duration,
        has_audio=True,
        audio_sample_rate=48000,
        audio_codec=codec,
    )


def _video_probe(duration=120.0, width=3840, height=2160):
    return SimpleNamespace(
        duration=duration,
        width=width,
        height=height,
        has_video=True,
        has_audio=True,
        audio_sample_rate=48000,
        audio_codec="opus",
    )


@pytest.mark.parametrize(
    ("suffix", "mime_type"),
    [
        (".aac", "audio/aac"),
        (".aiff", "audio/aiff"),
        (".flac", "audio/flac"),
        (".mp3", "audio/mp3"),
        (".ogg", "audio/ogg"),
        (".wav", "audio/wav"),
    ],
)
def test_factory_audio_mime_type_uses_supported_formats(suffix, mime_type):
    assert source.factory_audio_mime_type(Path("audio" + suffix)) == mime_type


def test_factory_audio_mime_type_rejects_unsupported_container():
    with pytest.raises(RuntimeError, match="not supported by Gemini"):
        source.factory_audio_mime_type(Path("audio.webm"))


def test_factory_audio_probe_requires_real_audio_evidence():
    assert source.factory_audio_probe_is_usable(_audio_probe()) is True
    assert source.factory_audio_probe_is_usable(None) is False
    assert source.factory_audio_probe_is_usable(
        SimpleNamespace(
            duration=120.0,
            has_audio=False,
            audio_sample_rate=48000,
            audio_codec="opus",
        )
    ) is False


@pytest.mark.asyncio
async def test_factory_audio_download_uses_native_best_and_compact_aac(
    monkeypatch,
    tmp_path,
):
    commands = []
    raw_path = tmp_path / "video_factory_audio_source.webm"

    async def fake_run(command, **kwargs):
        commands.append(list(command))
        if command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"a" * 4096)
        else:
            raw_path.write_bytes(b"o" * 4096)
        return SimpleNamespace(returncode=0, stderr="")

    async def fake_probe(path):
        return _audio_probe("aac" if path.suffix == ".aac" else "opus")

    monkeypatch.setattr(source, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(
        source,
        "YTDLP_BASE_ARGS",
        ["python", "-m", "yt_dlp", "--format-sort", "ext:mp4:m4a"],
    )
    monkeypatch.setattr(source, "run_cancellable_process", fake_run)
    monkeypatch.setattr(source, "probe_media_async", fake_probe)
    monkeypatch.setattr(source.shutil, "which", lambda name: "ffmpeg")

    result = await source._download_factory_audio_fresh(
        "https://youtu.be/example",
        "video",
    )

    yt_command, ffmpeg_command = commands
    assert result == tmp_path / "video_factory_audio_gemini.aac"
    assert result.exists()
    assert not raw_path.exists()
    assert "--format-sort-reset" in yt_command
    assert yt_command.index("--format-sort-reset") > yt_command.index("--format-sort")
    assert "--no-format-sort-force" in yt_command
    assert "--no-prefer-free-formats" in yt_command
    assert yt_command[yt_command.index("--format") + 1] == "bestaudio/best"
    assert "--extract-audio" not in yt_command
    assert "--audio-format" not in yt_command
    assert ffmpeg_command[ffmpeg_command.index("-c:a") + 1] == "aac"
    assert ffmpeg_command[ffmpeg_command.index("-b:a") + 1] == "128k"
    assert ffmpeg_command[ffmpeg_command.index("-ac") + 1] == "1"
    assert ffmpeg_command[ffmpeg_command.index("-ar") + 1] == "48000"
    assert ffmpeg_command[ffmpeg_command.index("-f") + 1] == "adts"


@pytest.mark.asyncio
async def test_factory_native_aac_is_normalized_to_analysis_contract(
    monkeypatch,
    tmp_path,
):
    commands = []
    raw_path = tmp_path / "native.m4a"
    raw_path.write_bytes(b"a" * 4096)

    async def fake_run(command, **kwargs):
        commands.append(list(command))
        Path(command[-1]).write_bytes(b"a" * 4096)
        return SimpleNamespace(returncode=0, stderr="")

    async def fake_probe(path):
        return _audio_probe("aac")

    monkeypatch.setattr(source, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(source, "run_cancellable_process", fake_run)
    monkeypatch.setattr(source, "probe_media_async", fake_probe)
    monkeypatch.setattr(source.shutil, "which", lambda name: "ffmpeg")

    result = await source._prepare_gemini_audio(
        raw_path,
        _audio_probe("aac"),
        "video",
    )

    assert result.suffix == ".aac"
    command = commands[0]
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-b:a") + 1] == "128k"
    assert command[command.index("-ac") + 1] == "1"
    assert command[command.index("-ar") + 1] == "48000"
    assert command[command.index("-f") + 1] == "adts"
    assert source.factory_audio_mime_type(result) == "audio/aac"


@pytest.mark.asyncio
async def test_factory_video_download_has_no_resolution_ceiling(
    monkeypatch,
    tmp_path,
):
    commands = []
    output_path = tmp_path / "video_factory_max_source.mkv"

    async def fake_run(command, **kwargs):
        commands.append(list(command))
        output_path.write_bytes(b"v" * 4096)
        return SimpleNamespace(returncode=0, stderr="")

    async def fake_probe(path):
        assert path == output_path
        return _video_probe()

    monkeypatch.setattr(
        source,
        "YTDLP_BASE_ARGS",
        ["python", "-m", "yt_dlp", "--format-sort", "ext:mp4:m4a"],
    )
    monkeypatch.setattr(source, "run_cancellable_process", fake_run)
    monkeypatch.setattr(source, "probe_media_async", fake_probe)

    result = await source.download_factory_video_source(
        "https://youtu.be/example",
        "video",
        workdir=tmp_path,
    )

    command = commands[0]
    assert result == output_path
    assert "--format-sort-reset" in command
    assert "--no-format-sort-force" in command
    assert "--no-prefer-free-formats" in command
    assert command[command.index("--format") + 1] == "bestvideo+bestaudio/best"
    assert "height<=720" not in " ".join(command)
    assert command[command.index("--merge-output-format") + 1] == "mkv"


@pytest.mark.asyncio
async def test_factory_livedub_uses_maximum_original_and_full_tail(
    monkeypatch,
    tmp_path,
):
    original_path = tmp_path / "translated_factory_max_source.mkv"
    ru_path = tmp_path / "yandex_live.mp3"
    final_path = tmp_path / "factory_max_livedub.mp4"
    original_path.write_bytes(b"v" * 4096)
    ru_path.write_bytes(b"r" * 4096)

    async def fake_download_original(
        url,
        media_id,
        workdir=None,
        *,
        expected_duration=0.0,
    ):
        assert media_id == "translated"
        assert expected_duration == 100.0
        return original_path

    async def fake_get_live_audio(*args, **kwargs):
        assert kwargs["voice_style"] == "live"
        assert kwargs["timeout"] == 1800
        return ru_path

    async def fake_mix(original, ru_audio, output):
        assert original == original_path
        assert ru_audio == ru_path
        assert output == final_path
        output.write_bytes(b"m" * 4096)
        return output

    async def fake_probe(path):
        if path == original_path:
            return _video_probe(duration=100.0)
        if path == final_path:
            return _video_probe(duration=101.6)
        raise AssertionError(path)

    monkeypatch.setenv("SHORTS_FACTORY_TRANSLATION_BACKEND", "yandex_live")
    monkeypatch.setenv("SHORTS_FACTORY_LIVEDUB", "1")
    monkeypatch.setenv("SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC", "60")
    monkeypatch.setattr(source, "ensure_factory_video_space", lambda *args, **kwargs: None)
    monkeypatch.setattr(source, "download_factory_video_source", fake_download_original)
    monkeypatch.setattr(yandex_live_dub, "get_live_dub_audio", fake_get_live_audio)
    monkeypatch.setattr(livedub_mix, "mix_tracks", fake_mix)
    monkeypatch.setattr(livedub_mix, "get_mix_params", lambda: {"tail_pad_ms": 1600})
    monkeypatch.setattr(source, "probe_media_async", fake_probe)

    result = await source.prepare_factory_translation_video(
        "https://youtu.be/example",
        tmp_path,
        100,
        "en",
    )

    assert result == final_path


@pytest.mark.asyncio
async def test_factory_livedub_rejects_missing_required_tail(
    monkeypatch,
    tmp_path,
):
    original_path = tmp_path / "translated_factory_max_source.mkv"
    ru_path = tmp_path / "yandex_live.mp3"
    final_path = tmp_path / "factory_max_livedub.mp4"
    original_path.write_bytes(b"v" * 4096)
    ru_path.write_bytes(b"r" * 4096)

    async def fake_download_original(*args, **kwargs):
        assert kwargs["expected_duration"] == 100.0
        return original_path

    async def fake_get_live_audio(*args, **kwargs):
        return ru_path

    async def fake_mix(original, ru_audio, output):
        output.write_bytes(b"m" * 4096)
        return output

    async def fake_probe(path):
        if path == original_path:
            return _video_probe(duration=100.0)
        if path == final_path:
            return _video_probe(duration=101.2)
        raise AssertionError(path)

    monkeypatch.setenv("SHORTS_FACTORY_TRANSLATION_BACKEND", "yandex_live")
    monkeypatch.setenv("SHORTS_FACTORY_LIVEDUB", "1")
    monkeypatch.setattr(source, "ensure_factory_video_space", lambda *args, **kwargs: None)
    monkeypatch.setattr(source, "download_factory_video_source", fake_download_original)
    monkeypatch.setattr(yandex_live_dub, "get_live_dub_audio", fake_get_live_audio)
    monkeypatch.setattr(livedub_mix, "mix_tracks", fake_mix)
    monkeypatch.setattr(livedub_mix, "get_mix_params", lambda: {"tail_pad_ms": 1600})
    monkeypatch.setattr(source, "probe_media_async", fake_probe)

    with pytest.raises(RuntimeError, match="lost the required Russian tail"):
        await source.prepare_factory_translation_video(
            "https://youtu.be/example",
            tmp_path,
            100,
            "en",
        )


@pytest.mark.asyncio
async def test_factory_plan_passes_real_prepared_audio_mime(monkeypatch, tmp_path):
    captured_mimes = []
    audio_path = tmp_path / "source.aac"
    audio_path.write_bytes(b"a" * 4096)

    class FakePart:
        @staticmethod
        def from_bytes(*, data, mime_type):
            assert data
            captured_mimes.append(mime_type)
            return {"mime_type": mime_type}

    fake_types = SimpleNamespace(Part=FakePart)

    async def fake_run_pass(*args, **kwargs):
        prompt = kwargs["prompt"]
        if prompt == "audit":
            return {"metadata": {"language": "en"}, "stage": "audit"}
        return {"metadata": {"language": "en"}, "stage": prompt}

    def fake_validate(*args, **kwargs):
        return {
            "metadata": {"language": "en"},
            "shorts_candidates": [{
                "title": "Candidate",
                "hook": "Hook",
                "reason": "Reason",
                "quality_score": 99,
                "boundary_verified": True,
                "start_seconds": 1.125,
                "end_seconds": 40.625,
            }],
            "long_candidates": [],
        }

    monkeypatch.setattr(capacity, "factory_gemini_clients", lambda: [SimpleNamespace()])
    monkeypatch.setattr(candidates, "types", fake_types)
    monkeypatch.setattr(candidates, "shorts_factory_model", lambda: "gemini-3.7-flash")
    monkeypatch.setattr(candidates, "_run_pass", fake_run_pass)
    monkeypatch.setattr(candidates, "_scout_prompt", lambda *args: "scout")
    monkeypatch.setattr(candidates, "_judge_prompt", lambda *args: "judge")
    monkeypatch.setattr(candidates, "_boundary_prompt", lambda *args: "audit")
    monkeypatch.setattr(source, "_strict_boundary_prompt", lambda prompt: prompt)
    monkeypatch.setattr(candidates, "validate_factory_plan", fake_validate)

    plan = await source.create_factory_plan_from_supported_audio(
        audio_path,
        title="Title",
        performer="Author",
        duration=100,
    )

    assert captured_mimes == ["audio/aac"]
    assert plan["audio_mime_type"] == "audio/aac"
    assert plan["model"] == "gemini-3.7-flash"
    assert plan["thinking_level"] == "high"
    assert plan["review_passes"] == 3


def test_source_policy_is_directly_owned_without_installation():
    source_code = Path("services/shorts_factory_source.py").read_text(encoding="utf-8")
    quality = Path("services/shorts_factory_quality_gate.py").read_text(encoding="utf-8")
    capacity_runtime = Path("services/shorts_factory_capacity_runtime.py").read_text(
        encoding="utf-8"
    )

    assert "height<=720" not in source_code
    assert "height<=1080" not in source_code
    assert "get_live_dub_video" not in source_code
    assert "--extract-audio" not in source_code
    assert "--audio-format" not in source_code
    assert "download_factory_audio_with_retry_cache" in source_code
    assert "create_factory_plan_resumable" in source_code
    assert "apply_factory_quality_gate" in capacity_runtime
    assert "validated_factory_plan_language" in capacity_runtime
    assert "install_factory_source_quality_policy" not in source_code
    assert "install_factory_plan_quality_gate" not in quality
    assert "sys.modules" not in source_code
