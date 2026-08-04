from pathlib import Path

import pytest

from services.shorts_factory_no_downgrade import (
    MIN_FACTORY_FREE_GB,
    MIN_FACTORY_LIVEDUB_TIMEOUT_SEC,
    REQUIRED_FACTORY_WHISPER_MODEL,
    enforce_quality_floor,
    hardened_factory_subtitle_profile,
    precise_factory_seconds,
    require_factory_model_floor,
    resolve_factory_silence_end,
)


@pytest.mark.parametrize(
    "model",
    [
        "gemini-3.1-pro-preview",
        "gemini-3.2-pro",
        "gemini-4-pro-preview",
    ],
)
def test_factory_model_floor_accepts_only_current_or_newer_pro(model):
    assert require_factory_model_floor(model) == model


@pytest.mark.parametrize(
    "model",
    [
        "gemini-2.5-pro",
        "gemini-3-pro-preview",
        "gemini-3.1-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.1-preview-pro",
        "gemini-3.1-proxy-pro",
        "gemini-pro",
        "my-gemini-3.1-proxy",
        "",
    ],
)
def test_factory_model_floor_rejects_old_or_non_pro_routes(model):
    with pytest.raises(RuntimeError, match="SHORTS FACTORY MAX"):
        require_factory_model_floor(model)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (10.3754, 10.375),
        ("10.3754", 10.375),
        ("1:40.625", 100.625),
        ("1:02:03.125", 3723.125),
        (-5, 0.0),
        ("nan", 0.0),
        ("inf", 0.0),
        ("broken", 0.0),
    ],
)
def test_factory_timestamps_preserve_millisecond_precision(value, expected):
    assert precise_factory_seconds(value) == expected


def test_factory_quality_floors_can_only_be_tightened():
    assert enforce_quality_floor(70, 88, 100) == 88
    assert enforce_quality_floor(95, 88, 100) == 95
    assert enforce_quality_floor(120, 88, 100) == 100
    assert enforce_quality_floor("bad", 88, 100) == 88
    assert enforce_quality_floor(0.25, 0.25, 30) == 0.25
    assert enforce_quality_floor(0, 0.25, 30) == 0.25
    assert enforce_quality_floor(0.5, MIN_FACTORY_FREE_GB, 100) == 2.0
    assert (
        enforce_quality_floor(
            60,
            MIN_FACTORY_LIVEDUB_TIMEOUT_SEC,
            7200,
        )
        == 1800
    )


def test_factory_subtitle_profile_requires_exact_large_v3():
    assert hardened_factory_subtitle_profile(
        {
            "model_name": "large-v3",
            "karaoke": False,
            "word_timestamps": False,
            "light": True,
            "gemini_hints": False,
        }
    ) == {
        "model_name": REQUIRED_FACTORY_WHISPER_MODEL,
        "karaoke": True,
        "word_timestamps": True,
        "light": False,
        "gemini_hints": True,
    }

    for model in ("large-v3-turbo", "medium", "small", ""):
        with pytest.raises(RuntimeError, match="quality downgrade"):
            hardened_factory_subtitle_profile({"model_name": model})


@pytest.mark.asyncio
async def test_factory_exact_end_bypasses_second_silence_snap():
    calls = []

    async def original(source_path, target_end, *args, **kwargs):
        calls.append((source_path, target_end, args, kwargs))
        return target_end + 9.75

    exact = await resolve_factory_silence_end(
        original,
        "source.mp4",
        177.625,
        search_window=8.0,
        factory_active=True,
    )

    assert exact == 177.625
    assert calls == []


@pytest.mark.asyncio
async def test_non_factory_modes_keep_existing_silence_snap():
    calls = []

    async def original(source_path, target_end, *args, **kwargs):
        calls.append((source_path, target_end, args, kwargs))
        return target_end + 2.25

    adjusted = await resolve_factory_silence_end(
        original,
        "source.mp4",
        100.5,
        search_window=8.0,
        factory_active=False,
    )

    assert adjusted == 102.75
    assert calls == [("source.mp4", 100.5, (), {"search_window": 8.0})]


def test_required_installer_orders_no_downgrade_before_factory_execution():
    quality = Path("services/shorts_factory_quality_gate.py").read_text(
        encoding="utf-8"
    )
    no_downgrade = Path(
        "services/shorts_factory_no_downgrade.py"
    ).read_text(encoding="utf-8")

    source_pos = quality.index("if not install_cut_mode_source_policy():")
    replay_pos = quality.index("if not install_cut_replay_delivery_policy():")
    no_downgrade_pos = quality.index(
        "if not install_factory_no_downgrade_policy():"
    )
    execution_pos = quality.index(
        "if not install_shorts_factory_execution_guard():"
    )

    assert source_pos < replay_pos < no_downgrade_pos < execution_pos
    assert "candidates_module._seconds = precise_factory_seconds" in no_downgrade
    assert "shorts_video_module._find_silence_end" in no_downgrade
    assert "render_clips_module._find_silence_end" in no_downgrade
    assert "\ninstall_factory_no_downgrade_policy()\n" not in no_downgrade


def test_bad_factory_configuration_is_validated_before_any_module_patch():
    source = Path("services/shorts_factory_no_downgrade.py").read_text(
        encoding="utf-8"
    )

    model_validation = source.index(
        "validated_model = require_factory_model_floor("
    )
    profile_validation = source.index(
        "validated_profile = hardened_factory_subtitle_profile("
    )
    first_patch = source.index(
        "candidates_module.shorts_factory_model = strict_model_selector"
    )

    assert model_validation < first_patch
    assert profile_validation < first_patch


def test_factory_router_preserves_command_and_playlist_entrypoint_chains():
    source = Path("services/shorts_factory_runtime.py").read_text(
        encoding="utf-8"
    )

    assert "original_commands_process = commands_module.process_single_video" in source
    assert "original_playlist_process = playlist_module.process_single_video" in source
    assert "commands_process_link_by_mode = _wrap_link_by_mode(" in source
    assert "playlist_process_link_by_mode = _wrap_link_by_mode(" in source
    assert (
        "commands_module.process_single_video = commands_process_link_by_mode"
        in source
    )
    assert (
        "playlist_module.process_single_video = playlist_process_link_by_mode"
        in source
    )
