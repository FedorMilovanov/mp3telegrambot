from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import services.translation_editorial_factory as factory
from services.translation_editorial import build_review_pack, load_pack_manifest, sha256_file


def _write_srt(path: Path, text: str) -> Path:
    path.write_text(
        f"1\n00:00:01,000 --> 00:00:03,000\n{text}\n\n",
        encoding="utf-8",
    )
    return path


def _pack(tmp_path: Path, *, with_candidates: bool = False) -> Path:
    video = tmp_path / "translated.mp4"
    video.write_bytes(b"translated-source")
    original = _write_srt(tmp_path / "original.srt", "Faith apart from works.")
    russian = _write_srt(tmp_path / "russian.srt", "Вера отдельно от дел.")
    shorts = [{"title": "Works", "start_seconds": 0.0, "end_seconds": 4.0}] if with_candidates else []
    return build_review_pack(
        output_dir=tmp_path,
        media_id="video123",
        source_url="https://example.invalid/video123",
        title="Sermon",
        performer="Preacher",
        duration=10.0,
        source_video_path=video,
        original_srt_path=original,
        russian_whisper_srt_path=russian,
        shorts_candidates=shorts,
        timeline_metadata={
            "original_srt": "source_original_timeline",
            "russian_whisper": "translated_video_timeline",
            "configured_russian_delay_seconds": 0.6,
        },
    )


def test_factory_editorial_defaults_pack_on_gemini_off_and_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SHORTS_FACTORY_EDITORIAL_REVIEW_PACK", raising=False)
    monkeypatch.delenv("SHORTS_FACTORY_EDITORIAL_GEMINI", raising=False)
    monkeypatch.delenv("SHORTS_FACTORY_EDITORIAL_GEMINI_MAX_ATTEMPTS", raising=False)

    assert factory.factory_editorial_pack_enabled() is True
    assert factory.factory_editorial_gemini_enabled() is False
    assert factory.FACTORY_EDITORIAL_GEMINI_MODEL == "gemini-3.7-flash"
    assert factory._gemini_max_attempts() == 1


def test_gemini_attempt_override_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHORTS_FACTORY_EDITORIAL_GEMINI_MAX_ATTEMPTS", "99")
    assert factory._gemini_max_attempts() == 2
    monkeypatch.setenv("SHORTS_FACTORY_EDITORIAL_GEMINI_MAX_ATTEMPTS", "bad")
    assert factory._gemini_max_attempts() == 1


def test_timeline_metadata_tracks_configured_factory_alignment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEDUB_DELAY_MS", "750")
    monkeypatch.setenv("SHORTS_FACTORY_LIVEDUB_SHIFT_EXTRA_SEC", "0.2")

    timeline = factory._timeline_metadata()

    assert timeline["original_srt"] == "source_original_timeline"
    assert timeline["russian_whisper"] == "translated_video_timeline"
    assert timeline["factory_candidates"] == "translated_video_timeline"
    assert timeline["configured_russian_delay_seconds"] == 0.75
    assert timeline["factory_candidate_extra_shift_seconds"] == 0.2


def test_editorial_root_id_is_sanitized() -> None:
    assert factory._safe_media_id("../bad/path") == "___bad_path"
    assert "/" not in factory._safe_media_id("a/b")
    assert "\\" not in factory._safe_media_id("a\\b")


def test_durable_review_source_survives_factory_cache_removal(tmp_path: Path) -> None:
    source = tmp_path / "video123_factory_source.mp4"
    source.write_bytes(b"source-bytes" * 300)
    root = tmp_path / "review"
    root.mkdir()

    durable = factory._durable_review_source(source, root, "video123")
    original_sha = sha256_file(source)
    source.unlink()

    assert durable.exists()
    assert "_factory_source" not in durable.name
    assert sha256_file(durable) == original_sha


def test_render_review_markdown_is_readable() -> None:
    text = factory.render_review_markdown(
        {
            "full_sermon": {
                "verdict": "repair",
                "reason": "Один локальный дефект.",
                "issues": [
                    {
                        "start_seconds": 12.4,
                        "end_seconds": 12.8,
                        "severity": "major",
                        "category": "semantic_term",
                        "rationale": "Смысл термина искажён.",
                        "action": {"type": "reject_region"},
                    }
                ],
            },
            "candidate_reviews": [
                {"candidate_id": "short:1", "verdict": "keep", "reason": "Чисто."}
            ],
        }
    )

    assert "Full sermon: **REPAIR**" in text
    assert "12.400–12.800s" in text
    assert "`short:1` — **KEEP**" in text


@pytest.mark.asyncio
async def test_gemini_review_uses_exact_36_high_once_without_sampling_or_local_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = _pack(tmp_path)
    manifest = load_pack_manifest(pack)
    local_path = manifest["source"]["translated_video"]["local_path"]
    calls: list[dict] = []
    configs: list[dict] = []

    payload = {
        "full_sermon": {
            "verdict": "keep",
            "reason": "Смысл сохранён.",
            "issues": [],
        },
        "candidate_reviews": [],
    }

    class FakeModels:
        async def generate_content(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))

    fake_client = SimpleNamespace(aio=SimpleNamespace(models=FakeModels()))

    def fake_config(**kwargs):
        configs.append(kwargs)
        return {"cfg": True}

    import core.globals as globals_module

    monkeypatch.delenv("SHORTS_FACTORY_EDITORIAL_GEMINI_MAX_ATTEMPTS", raising=False)
    monkeypatch.setattr(globals_module, "GEMINI_CLIENTS", [fake_client, fake_client, fake_client])
    monkeypatch.setattr(globals_module, "make_text_config_smart", fake_config)

    review = await factory.generate_gemini_editorial_review(pack)

    assert review is not None
    assert review["reviewer"] == "gemini:gemini-3.7-flash"
    assert len(calls) == 1
    assert calls[0]["model"] == "gemini-3.7-flash"
    assert "ORIGINAL SRT" in calls[0]["contents"]
    assert "RUSSIAN WHISPER SRT" in calls[0]["contents"]
    assert "different" not in calls[0]["contents"].lower()  # prompt is Russian/pattern-level
    assert "одинаковому номеру cue" in calls[0]["contents"]
    assert local_path not in calls[0]["contents"]
    assert configs == [
        {
            "max_output_tokens": 12000,
            "model_name": "gemini-3.7-flash",
            "thinking_level": "high",
            "response_mime_type": "application/json",
            "response_schema": factory._gemini_schema(),
        }
    ]
    assert "temperature" not in configs[0]
    assert "top_p" not in configs[0]
    assert "top_k" not in configs[0]


@pytest.mark.asyncio
async def test_gemini_review_missing_candidate_is_rejected_without_hidden_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = _pack(tmp_path, with_candidates=True)
    calls: list[dict] = []
    payload = {
        "full_sermon": {"verdict": "keep", "reason": "ok", "issues": []},
        "candidate_reviews": [],
    }

    class FakeModels:
        async def generate_content(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text=json.dumps(payload, ensure_ascii=False))

    fake_client = SimpleNamespace(aio=SimpleNamespace(models=FakeModels()))

    import core.globals as globals_module

    monkeypatch.delenv("SHORTS_FACTORY_EDITORIAL_GEMINI_MAX_ATTEMPTS", raising=False)
    monkeypatch.setattr(globals_module, "GEMINI_CLIENTS", [fake_client, fake_client])
    monkeypatch.setattr(globals_module, "make_text_config_smart", lambda **_kwargs: {})

    assert await factory.generate_gemini_editorial_review(pack) is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_original_srt_manual_is_preferred_before_auto(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    async def fake_run(command, **_kwargs):
        commands.append(list(command))
        output_index = command.index("--output") + 1
        template = Path(command[output_index])
        generated = Path(str(template).replace("%(id)s", "abc").replace("%(ext)s", "en.srt"))
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text("1\n00:00:00,000 --> 00:00:01,000\nSource\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(factory, "run_cancellable_process", fake_run)

    result = await factory.download_original_srt(
        "https://example.invalid/watch?v=abc",
        tmp_path,
        language="en",
    )

    assert result.exists()
    assert len(commands) == 1
    assert "--write-subs" in commands[0]
    assert "--write-auto-subs" not in commands[0]
    languages = commands[0][commands[0].index("--sub-langs") + 1]
    assert languages == "en.*,en"


def test_review_pack_contains_real_transcripts_not_video_bytes(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    with zipfile.ZipFile(pack, "r") as archive:
        names = set(archive.namelist())
        original = archive.read("original.srt").decode("utf-8")
        russian = archive.read("russian_whisper.srt").decode("utf-8")

    assert "Faith apart from works." in original
    assert "Вера отдельно от дел." in russian
    assert not any(name.endswith(".mp4") for name in names)


def test_immutable_gemini_review_filename_binds_pack_and_review(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    manifest = load_pack_manifest(pack)
    review = {
        "schema_name": "mp3telegrambot.translation-editorial-review",
        "schema_version": 1,
        "review_pack_id": manifest["review_pack_id"],
        "reviewer": "gemini:gemini-3.7-flash",
        "full_sermon": {"verdict": "keep", "reason": "ok", "issues": []},
        "candidate_reviews": [],
    }

    json_path, markdown_path = factory._write_immutable_review_files(
        tmp_path,
        "video123",
        pack,
        review,
    )
    second_json, second_markdown = factory._write_immutable_review_files(
        tmp_path,
        "video123",
        pack,
        review,
    )

    assert json_path == second_json
    assert markdown_path == second_markdown
    assert manifest["review_pack_id"][7:19] in json_path.name
