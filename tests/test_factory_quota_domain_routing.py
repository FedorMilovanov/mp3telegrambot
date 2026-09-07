from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services import shorts_factory_capacity as capacity
from services import shorts_factory_capacity_runtime as runtime


class _ServiceError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def _install_factory_stubs(monkeypatch, run_pass):
    import services.shorts_factory_candidates as candidates
    import services.shorts_factory_quality_gate as quality_gate
    import services.shorts_factory_source as source

    async def verified_duration(_path):
        return 120.0

    monkeypatch.setattr(
        candidates,
        "types",
        SimpleNamespace(
            Part=SimpleNamespace(
                from_bytes=lambda *, data, mime_type: SimpleNamespace(
                    data=data,
                    mime_type=mime_type,
                )
            ),
            UploadFileConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        ),
    )
    monkeypatch.setattr(candidates, "shorts_factory_model", lambda: "gemini-3.8-flash")
    monkeypatch.setattr(candidates, "_run_pass", run_pass)
    monkeypatch.setattr(candidates, "_scout_prompt", lambda *args: "scout")
    monkeypatch.setattr(candidates, "_judge_prompt", lambda *args: "judge")
    monkeypatch.setattr(candidates, "_boundary_prompt", lambda *args: "boundary")
    monkeypatch.setattr(
        candidates,
        "validate_factory_plan",
        lambda *args, **kwargs: {
            "shorts_candidates": [{"start": 1.0, "end": 10.0}],
            "long_candidates": [],
        },
    )
    monkeypatch.setattr(quality_gate, "apply_factory_quality_gate", lambda plan: plan)
    monkeypatch.setattr(quality_gate, "validated_factory_plan_language", lambda plan: "ru")
    monkeypatch.setattr(source, "factory_audio_mime_type", lambda path: "audio/flac")
    monkeypatch.setattr(source, "measure_factory_audio_duration", verified_duration)
    monkeypatch.setattr(
        source,
        "factory_duration_matches",
        lambda actual, expected: abs(float(actual) - float(expected)) <= 2.0,
    )


def test_project_scope_detector_requires_explicit_project_quota_name() -> None:
    assert capacity.factory_project_quota_error(
        _ServiceError(
            429,
            "RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel-FreeTier",
        )
    ) is True
    assert capacity.factory_project_quota_error(
        _ServiceError(429, "RESOURCE_EXHAUSTED")
    ) is False
    assert capacity.factory_project_quota_error(
        _ServiceError(503, "UNAVAILABLE: high demand PerProject")
    ) is False


def test_same_explicit_project_domain_is_skipped_after_project_scoped_429(
    monkeypatch,
    tmp_path,
) -> None:
    audio = tmp_path / "factory.flac"
    audio.write_bytes(b"x" * 2048)
    first = SimpleNamespace(name="first")
    second = SimpleNamespace(name="second")
    third = SimpleNamespace(name="third")
    calls: list[str] = []

    async def run_pass(client, **kwargs):
        calls.append(client.name)
        if client is first:
            raise _ServiceError(
                429,
                "RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel-FreeTier",
            )
        return {"ok": True}

    _install_factory_stubs(monkeypatch, run_pass)
    monkeypatch.setattr(capacity, "factory_gemini_clients", lambda: [first, second, third])
    monkeypatch.setattr(
        capacity,
        "factory_gemini_quota_domains",
        lambda: ["project-a", "project-a", "project-b"],
    )

    plan = asyncio.run(
        runtime.create_factory_plan_resumable(
            audio,
            title="Title",
            performer="Author",
            duration=120,
        )
    )

    assert calls == ["first", "third", "third", "third"]
    assert plan["model"] == "gemini-3.8-flash"
    assert plan["thinking_level"] == "high"
    assert plan["review_passes"] == 3
    assert plan["strict_quality"] is True


def test_generic_429_does_not_skip_same_labeled_domain(monkeypatch, tmp_path) -> None:
    audio = tmp_path / "factory.flac"
    audio.write_bytes(b"x" * 2048)
    first = SimpleNamespace(name="first")
    second = SimpleNamespace(name="second")
    calls: list[str] = []

    async def run_pass(client, **kwargs):
        calls.append(client.name)
        if client is first:
            raise _ServiceError(429, "RESOURCE_EXHAUSTED")
        return {"ok": True}

    _install_factory_stubs(monkeypatch, run_pass)
    monkeypatch.setattr(capacity, "factory_gemini_clients", lambda: [first, second])
    monkeypatch.setattr(
        capacity,
        "factory_gemini_quota_domains",
        lambda: ["project-a", "project-a"],
    )

    plan = asyncio.run(
        runtime.create_factory_plan_resumable(
            audio,
            title="Title",
            performer="Author",
            duration=120,
        )
    )

    assert calls == ["first", "second", "second", "second"]
    assert plan["review_passes"] == 3


def test_all_same_domain_project_quota_reports_skipped_credentials(
    monkeypatch,
    tmp_path,
) -> None:
    audio = tmp_path / "factory.flac"
    audio.write_bytes(b"x" * 2048)
    first = SimpleNamespace(name="first")
    second = SimpleNamespace(name="second")
    third = SimpleNamespace(name="third")
    calls: list[str] = []

    async def run_pass(client, **kwargs):
        calls.append(client.name)
        raise _ServiceError(
            429,
            "RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel-FreeTier",
        )

    _install_factory_stubs(monkeypatch, run_pass)
    monkeypatch.setattr(capacity, "factory_gemini_clients", lambda: [first, second, third])
    monkeypatch.setattr(
        capacity,
        "factory_gemini_quota_domains",
        lambda: ["project-a", "project-a", "project-a"],
    )

    with pytest.raises(RuntimeError) as raised:
        asyncio.run(
            runtime.create_factory_plan_resumable(
                audio,
                title="Title",
                performer="Author",
                duration=120,
            )
        )

    assert calls == ["first"]
    message = str(raised.value)
    assert "attempted=1/3" in message
    assert "same_project_domain_skipped=2" in message
    assert "project-a" not in message


def test_misaligned_domain_metadata_fails_open_to_legacy_rotation(monkeypatch, tmp_path) -> None:
    audio = tmp_path / "factory.flac"
    audio.write_bytes(b"x" * 2048)
    first = SimpleNamespace(name="first")
    second = SimpleNamespace(name="second")
    calls: list[str] = []

    async def run_pass(client, **kwargs):
        calls.append(client.name)
        if client is first:
            raise _ServiceError(
                429,
                "RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel-FreeTier",
            )
        return {"ok": True}

    _install_factory_stubs(monkeypatch, run_pass)
    monkeypatch.setattr(capacity, "factory_gemini_clients", lambda: [first, second])
    monkeypatch.setattr(capacity, "factory_gemini_quota_domains", lambda: ["project-a"])

    plan = asyncio.run(
        runtime.create_factory_plan_resumable(
            audio,
            title="Title",
            performer="Author",
            duration=120,
        )
    )

    assert calls == ["first", "second", "second", "second"]
    assert plan["strict_quality"] is True


def test_key_domain_entries_deduplicate_without_exposing_key_material(
    monkeypatch,
) -> None:
    from core import globals as core_globals

    monkeypatch.setattr(core_globals, "GEMINI_API_KEY", "secret-key-a")
    monkeypatch.setattr(core_globals, "GEMINI_API_KEY_2", "secret-key-a")
    monkeypatch.setattr(core_globals, "GEMINI_API_KEY_3", "secret-key-b")
    monkeypatch.setattr(core_globals, "GEMINI_API_KEY_4", "")
    monkeypatch.setenv("GEMINI_QUOTA_DOMAIN", "")
    monkeypatch.setenv("GEMINI_QUOTA_DOMAIN_2", "Project-A")
    monkeypatch.setenv("GEMINI_QUOTA_DOMAIN_3", "Project-B")
    monkeypatch.delenv("GEMINI_QUOTA_DOMAIN_4", raising=False)

    assert capacity._factory_api_keys() == ["secret-key-a", "secret-key-b"]
    assert capacity.factory_gemini_quota_domains() == ["project-a", "project-b"]


def test_conflicting_duplicate_key_domains_and_unsafe_labels_fail_closed(
    monkeypatch,
) -> None:
    from core import globals as core_globals

    monkeypatch.setattr(core_globals, "GEMINI_API_KEY", "same-secret")
    monkeypatch.setattr(core_globals, "GEMINI_API_KEY_2", "same-secret")
    monkeypatch.setattr(core_globals, "GEMINI_API_KEY_3", "")
    monkeypatch.setattr(core_globals, "GEMINI_API_KEY_4", "")
    monkeypatch.setenv("GEMINI_QUOTA_DOMAIN", "project-a")
    monkeypatch.setenv("GEMINI_QUOTA_DOMAIN_2", "project-b")

    with pytest.raises(RuntimeError, match="conflicting GEMINI_QUOTA_DOMAIN"):
        capacity.factory_gemini_quota_domains()

    monkeypatch.setattr(core_globals, "GEMINI_API_KEY_2", "")
    monkeypatch.setenv("GEMINI_QUOTA_DOMAIN", "project id with spaces")
    with pytest.raises(RuntimeError, match="GEMINI_QUOTA_DOMAIN labels"):
        capacity.factory_gemini_quota_domains()
