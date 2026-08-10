from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import services.translation_editorial_factory as factory
from services.translation_editorial import build_review_pack


def _write_srt(path: Path, text: str) -> Path:
    path.write_text(
        f"1\n00:00:01,000 --> 00:00:03,000\n{text}\n\n",
        encoding="utf-8",
    )
    return path


def _pack(tmp_path: Path) -> Path:
    video = tmp_path / "translated.mp4"
    video.write_bytes(b"translated-source")
    original = _write_srt(tmp_path / "original.srt", "Faith apart from works.")
    russian = _write_srt(tmp_path / "russian.srt", "Вера отдельно от дел.")
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
    )


def test_factory_editorial_defaults_pack_on_and_gemini_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHORTS_FACTORY_EDITORIAL_REVIEW_PACK", raising=False)
    monkeypatch.delenv("SHORTS_FACTORY_EDITORIAL_GEMINI", raising=False)

    assert factory.factory_editorial_pack_enabled() is True
    assert factory.factory_editorial_gemini_enabled() is False
    assert factory.FACTORY_EDITORIAL_GEMINI_MODEL == "gemini-3.6-flash"


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
async def test_gemini_review_uses_exact_36_high_once_without_sampling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = _pack(tmp_path)
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

    monkeypatch.setattr(globals_module, "GEMINI_CLIENTS", [fake_client])
    monkeypatch.setattr(globals_module, "make_text_config_smart", fake_config)

    review = await factory.generate_gemini_editorial_review(pack)

    assert review is not None
    assert review["reviewer"] == "gemini:gemini-3.6-flash"
    assert len(calls) == 1
    assert calls[0]["model"] == "gemini-3.6-flash"
    assert "ORIGINAL SRT" in calls[0]["contents"]
    assert "RUSSIAN WHISPER SRT" in calls[0]["contents"]
    assert configs == [
        {
            "max_output_tokens": 12000,
            "model_name": "gemini-3.6-flash",
            "thinking_level": "high",
            "response_mime_type": "application/json",
            "response_schema": factory._gemini_schema(),
        }
    ]
    assert "temperature" not in configs[0]
    assert "top_p" not in configs[0]
    assert "top_k" not in configs[0]


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


def test_review_pack_contains_real_transcripts_not_video_bytes(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    with zipfile.ZipFile(pack, "r") as archive:
        names = set(archive.namelist())
        original = archive.read("original.srt").decode("utf-8")
        russian = archive.read("russian_whisper.srt").decode("utf-8")

    assert "Faith apart from works." in original
    assert "Вера отдельно от дел." in russian
    assert not any(name.endswith(".mp4") for name in names)
