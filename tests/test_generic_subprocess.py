from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.voxcpm2 import generic_project_runtime as project
from tools.voxcpm2 import generic_short_production as pipeline


def _backend(cpu_python: Path) -> SimpleNamespace:
    return SimpleNamespace(
        backend_id="test-backend",
        capabilities=lambda: SimpleNamespace(missing=lambda: []),
        runtime_paths=lambda repo, request: SimpleNamespace(cpu_python=cpu_python),
        process_environment=lambda *args, **kwargs: SimpleNamespace(as_dict=lambda base: {"TEST_SPEECH_BACKEND": "1"}),
        build_renderer_command=lambda runtime, values: [str(cpu_python), "renderer"],
        build_master_command=lambda runtime, values: [str(cpu_python), "master"],
    )


def test_run_checked_executes_internal_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="ok\n", stderr=""))
    monkeypatch.setattr(pipeline.subprocess, "run", run)

    result = pipeline.run_checked(["/resolved/tool", "--version"], capture=True, timeout=5)

    assert result.stdout == "ok\n"
    assert run.call_args.args[0] == ["/resolved/tool", "--version"]
    assert run.call_args.kwargs["timeout"] == 5
    assert "shell" not in run.call_args.kwargs


def test_short_caption_download_executes_pinned_ytdlp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pipeline, "_ytdlp_base", lambda: ["/resolved/python", "-m", "yt_dlp"])

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        (tmp_path / "captions.en.vtt").write_text(
            "WEBVTT\n\n00:00:00.000 --> 00:00:03.000\nGod is good.\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    process = Mock(side_effect=run)
    monkeypatch.setattr(pipeline.subprocess, "run", process)

    cues = pipeline.download_captions("https://youtu.be/AbCdEf12345", tmp_path)

    assert [cue.text for cue in cues] == ["God is good."]
    command = process.call_args.args[0]
    assert command[:3] == ["/resolved/python", "-m", "yt_dlp"]
    assert command[-1] == "https://youtu.be/AbCdEf12345"
    assert "shell" not in process.call_args.kwargs


def test_short_main_executes_renderer_and_master_with_checked_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "short"
    cpu_python = tmp_path / "cpu-python"
    cpu_python.write_bytes(b"python")
    monkeypatch.setattr(pipeline, "configure_utf8", lambda: None)
    monkeypatch.setattr(pipeline, "require_tool", lambda name: f"/resolved/{name}")
    monkeypatch.setattr(
        pipeline.sys,
        "argv",
        [
            "generic-short",
            "--source-url",
            "https://youtu.be/AbCdEf12345",
            "--video-id",
            "AbCdEf12345",
            "--work-root",
            str(root),
            "--speech-backend",
            "test-backend",
        ],
    )
    monkeypatch.setattr(
        pipeline,
        "download_source",
        lambda url, source: {"title": "Title", "uploader": "Author"},
    )
    monkeypatch.setattr(pipeline, "ffprobe_duration", lambda path: 5.0)
    monkeypatch.setattr(
        pipeline,
        "download_captions",
        lambda url, source_dir: [pipeline.Cue(0.0, 5.0, "God is good.")],
    )
    monkeypatch.setattr(
        pipeline,
        "translate_groups",
        lambda groups, model_name: [{"id": 1, "russian": "Бог благ."}],
    )
    monkeypatch.setattr(pipeline, "build_reference", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "get_backend", lambda backend_id: _backend(cpu_python))
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(pipeline.subprocess, "run", run)

    pipeline.main()

    assert [call.args[0] for call in run.call_args_list] == [
        [str(cpu_python), "renderer"],
        [str(cpu_python), "master"],
    ]
    assert all("shell" not in call.kwargs for call in run.call_args_list)
    manifest = root / "output" / "AbCdEf12345_FINAL.manifest.json"
    assert json.loads(manifest.read_text(encoding="utf-8"))["segments"] == 1


def test_project_caption_download_executes_pinned_ytdlp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(project.pipeline, "_ytdlp_base", lambda: ["/resolved/python", "-m", "yt_dlp"])

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        (tmp_path / "preferred_caption.en.vtt").write_text(
            "WEBVTT\n\n00:00:00.000 --> 00:00:03.000\nFirst human line\nSecond human line\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    process = Mock(side_effect=run)
    monkeypatch.setattr(project.subprocess, "run", process)

    cues = project._download_track(
        "https://youtu.be/AbCdEf12345",
        tmp_path,
        kind="manual",
        language="en",
    )

    assert [cue.text for cue in cues] == ["First human line Second human line"]
    command = process.call_args.args[0]
    assert command[:3] == ["/resolved/python", "-m", "yt_dlp"]
    assert command[-1] == "https://youtu.be/AbCdEf12345"
    assert "shell" not in process.call_args.kwargs


def test_project_renderer_and_master_execute_with_checked_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    cpu_python = tmp_path / "cpu-python"
    cpu_python.write_bytes(b"python")
    monkeypatch.setattr(project.pipeline, "build_reference", lambda *args, **kwargs: None)
    monkeypatch.setattr(project, "get_backend", lambda backend_id: _backend(cpu_python))
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(project.subprocess, "run", run)

    result = project._run_voxcpm_and_master(
        root=root,
        request={"speech_backend": "test-backend", "video_id": "AbCdEf12345"},
        source=source,
        cues=[project.pipeline.Cue(0.0, 5.0, "God is good.")],
        duration=5.0,
        segments_json=root / "segments.json",
        final_mixed=root / "mixed.mp4",
        final_russian=root / "russian.mp4",
    )

    assert result == root / "audio" / "AbCdEf12345_ru_timeline.wav"
    assert [call.args[0] for call in run.call_args_list] == [
        [str(cpu_python), "renderer"],
        [str(cpu_python), "master"],
    ]
    assert all("shell" not in call.kwargs for call in run.call_args_list)
