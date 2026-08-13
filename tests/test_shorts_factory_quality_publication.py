from pathlib import Path
from types import SimpleNamespace
import html

import pytest

import services.shorts_factory_portable_publication as publication
import services.shorts_factory_video_quality as quality
import services.shorts_factory_source as source


def _deliverable_probe(*, width=1920, height=1080, duration=120.0):
    return SimpleNamespace(
        duration=duration,
        width=width,
        height=height,
        has_video=True,
        has_audio=True,
        audio_sample_rate=48000,
        audio_codec="aac",
    )


def _factory_candidate(**overrides):
    item = {
        "title": "Тотальные Последствия Отрицания Воскресения Христа",
        "hashtags": ["#Христианство", "#ВоскресениеХриста", "#ВоддиБокам", "#Евангелие"],
        "start_seconds": 2309.0,
        "end_seconds": 3181.0,
        publication._CONTEXT_FIELD: {
            "source_full_title": "Необходимость Абсолютной Истины",
            "source_url": "https://www.youtube.com/watch?v=9EyYaiftkJc",
            "source_channel": "Example",
            "source_video_id": "9EyYaiftkJc",
        },
    }
    item.update(overrides)
    return item


def test_factory_source_is_capped_at_1080_while_generic_short_policy_stays_720():
    assert "height<=1080" in quality.FACTORY_VIDEO_FORMAT
    assert "height<=720" not in quality.FACTORY_VIDEO_FORMAT

    generic = Path("services/shorts_video_impl.py").read_text(encoding="utf-8")
    assert "bestvideo[height<=720]" in generic


@pytest.mark.asyncio
async def test_factory_1080_downloader_uses_verified_video_audio_master(
    monkeypatch,
    tmp_path,
):
    commands = []
    output = tmp_path / "video_factory_max_source.mkv"

    async def fake_run(command, **kwargs):
        commands.append(list(command))
        output.write_bytes(b"v" * 4096)
        return SimpleNamespace(returncode=0, stderr="")

    async def fake_probe(path):
        assert path == output
        return _deliverable_probe()

    async def no_evidence(*args, **kwargs):
        return None

    monkeypatch.setattr(quality, "YTDLP_BASE_ARGS", ["python", "-m", "yt_dlp"])
    monkeypatch.setattr(quality, "run_cancellable_process", fake_run)
    monkeypatch.setattr(quality, "probe_media_async", fake_probe)
    monkeypatch.setattr(quality, "media_probe_is_deliverable", lambda probe: probe is not None)
    monkeypatch.setattr(quality, "log_factory_media_evidence", no_evidence)
    monkeypatch.setattr(source, "_factory_quality_sort_reset", lambda: ["--format-sort", "hdr:0,res,fps"])
    monkeypatch.setattr(source, "_FACTORY_MEDIA_TIMEOUT_SEC", 60)

    result = await quality.download_factory_video_1080(
        "https://youtu.be/example",
        "video",
        workdir=tmp_path,
    )

    assert result == output
    command = commands[0]
    assert command[command.index("--format") + 1] == quality.FACTORY_VIDEO_FORMAT
    assert "height<=1080" in " ".join(command)
    assert command[command.index("--merge-output-format") + 1] == "mkv"


@pytest.mark.asyncio
async def test_factory_normalize_only_copies_video_stream(monkeypatch, tmp_path):
    commands = []
    input_path = tmp_path / "in.mp4"
    output_path = tmp_path / "out.mp4"
    input_path.write_bytes(b"input")

    async def fake_run(command, **kwargs):
        commands.append(list(command))
        output_path.write_bytes(b"output")
        return SimpleNamespace(returncode=0, stderr="")

    async def fake_probe(path):
        return _deliverable_probe(width=720, height=1280, duration=60)

    monkeypatch.setattr(quality.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(quality, "run_cancellable_process", fake_run)
    monkeypatch.setattr(quality, "probe_media_async", fake_probe)
    monkeypatch.setattr(quality, "media_probe_is_deliverable", lambda probe: probe is not None)

    assert quality.factory_normalize_only_uses_video_copy(
        normalize_audio=True,
        speed=1.0,
    )
    assert not quality.factory_normalize_only_uses_video_copy(
        normalize_audio=True,
        speed=1.1,
    )

    assert await quality.normalize_factory_short_audio_copy_video(
        input_path,
        output_path,
    )
    command = commands[0]
    video_codec_index = command.index("-c:v")
    assert command[video_codec_index + 1] == "copy"
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in command
    assert "h264_nvenc" not in command
    assert "libx264" not in command


def test_factory_long_profile_keeps_h264_quality_per_byte_controls():
    quality_args, preset_args = quality.factory_h264_nvenc_quality_args()

    assert quality_args[quality_args.index("-cq") + 1] == "25"
    assert quality_args[quality_args.index("-b:v") + 1] == "0"
    assert quality_args[quality_args.index("-spatial-aq") + 1] == "1"
    assert quality_args[quality_args.index("-rc-lookahead") + 1] == "20"
    assert preset_args == ["-preset", "p6", "-tune", "hq"]
    assert quality._long_scale_filter(1920, 1080) == ""
    scale = quality._long_scale_filter(3840, 2160)
    assert "1920" in scale and "1080" in scale
    assert "force_original_aspect_ratio=decrease" in scale


def test_factory_portable_caption_is_copy_ready_for_telegram_and_youtube():
    caption = publication.build_factory_portable_caption(
        candidate=_factory_candidate(),
        performer="Водди Бокам",
        real_author="Водди Бокам",
    )
    visible = html.unescape(caption)

    assert visible.startswith(
        "Тотальные Последствия Отрицания Воскресения Христа — Водди Бокам"
    )
    assert "🎙 Полная проповедь: «Необходимость Абсолютной Истины»" in visible
    assert "⏱ Фрагмент в полной проповеди: 38:29–53:01" in visible
    assert "▶️ https://www.youtube.com/watch?v=9EyYaiftkJc" in visible
    assert "#Христианство #ВоскресениеХриста #ВоддиБокам #Евангелие" in visible
    assert "<a href" not in caption
    assert "tg-emoji" not in caption
    assert "**" not in caption


def test_translated_factory_caption_uses_original_semantic_clock_not_render_clock():
    candidate = _factory_candidate(
        start_seconds=2311.4,
        end_seconds=3184.1,
        livedub_semantic_start_seconds=2309.0,
        livedub_semantic_end_seconds=3181.0,
    )

    visible = html.unescape(
        publication.build_factory_portable_caption(
            candidate=candidate,
            real_author="Водди Бокам",
        )
    )

    assert "38:29–53:01" in visible
    assert "38:31–53:04" not in visible


def test_factory_portable_caption_reserves_room_for_existing_publication_paragraph():
    candidate = _factory_candidate(
        title=("Очень длинный содержательный заголовок " * 40).strip(),
        hashtags=[
            "#ОченьДлинныйХэштегДляПроверкиОграничения" + str(index)
            for index in range(4)
        ],
    )

    caption = publication.build_factory_portable_caption(
        candidate=candidate,
        real_author="Водди Бокам",
    )
    visible = html.unescape(caption)

    assert len(visible) <= 690
    assert "Необходимость Абсолютной Истины" in visible
    assert "38:29–53:01" in visible
    assert "https://www.youtube.com/watch?v=9EyYaiftkJc" in visible


def test_factory_portable_wrapper_is_true_noop_for_non_factory_candidates():
    calls = []

    def original(*args, **kwargs):
        calls.append((args, kwargs))
        return "ORIGINAL"

    wrapped = publication.wrap_factory_portable_builder(original)
    assert wrapped(candidate={"title": "Обычный Short"}) == "ORIGINAL"
    assert len(calls) == 1


def test_factory_quality_policy_install_order_precedes_disk_and_execution_guards():
    gate = Path("services/shorts_factory_quality_gate.py").read_text(
        encoding="utf-8"
    )

    source_pos = gate.index("if not install_factory_source_quality_policy():")
    video_pos = gate.index("if not install_factory_video_quality_policy():")
    portable_pos = gate.index("if not install_factory_portable_publication():")
    disk_pos = gate.index("if not install_factory_disk_guard():")
    execution_pos = gate.index(
        "if not install_shorts_factory_execution_guard():"
    )

    assert source_pos < video_pos < portable_pos < disk_pos < execution_pos
