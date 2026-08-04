from pathlib import Path

import pytest

import services.cut_replay_delivery_policy as delivery
from services.cut_replay_delivery_policy import (
    mark_cut_replay_from_cache_decision,
    same_replay_audio_path,
)


def test_cache_decision_marks_replay_outside_source_context():
    token = delivery._REPLAY_OCCURRED.set(False)
    try:
        adjusted = mark_cut_replay_from_cache_decision(
            True,
            (False, "cut_cache_replay"),
        )
        assert adjusted == (False, "cut_cache_replay")
        assert delivery._REPLAY_OCCURRED.get() is True
    finally:
        delivery._REPLAY_OCCURRED.reset(token)


def test_non_replay_cache_decision_does_not_mark_delivery_contract():
    token = delivery._REPLAY_OCCURRED.set(False)
    try:
        mark_cut_replay_from_cache_decision(True, (True, "ok"))
        assert delivery._REPLAY_OCCURRED.get() is False
    finally:
        delivery._REPLAY_OCCURRED.reset(token)


def test_replay_audio_path_accepts_open_file_objects(tmp_path):
    main_mp3 = tmp_path / "main.mp3"
    other_mp3 = tmp_path / "other.mp3"
    main_mp3.write_bytes(b"main")
    other_mp3.write_bytes(b"other")

    with main_mp3.open("rb") as main_file, other_mp3.open("rb") as other_file:
        assert same_replay_audio_path(main_file, main_mp3) is True
        assert same_replay_audio_path(other_file, main_mp3) is False


@pytest.mark.asyncio
async def test_only_cut_proxy_reply_video_counts_as_replay_delivery():
    class Message:
        async def reply_video(self, *args, **kwargs):
            return "sent"

    replay_token = delivery._REPLAY_OCCURRED.set(True)
    counter_token = delivery._REPLAY_DELIVERIES.set([0])
    try:
        proxy = delivery._CutDeliveryMessageProxy(Message())
        assert await proxy.reply_video(video="cut.mp4") == "sent"
        assert delivery.cut_replay_delivery_count() == 1
    finally:
        delivery._REPLAY_DELIVERIES.reset(counter_token)
        delivery._REPLAY_OCCURRED.reset(replay_token)


@pytest.mark.asyncio
async def test_cut_proxy_does_not_count_normal_fresh_run():
    class Message:
        async def reply_video(self, *args, **kwargs):
            return "sent"

    replay_token = delivery._REPLAY_OCCURRED.set(False)
    counter_token = delivery._REPLAY_DELIVERIES.set([0])
    try:
        proxy = delivery._CutDeliveryMessageProxy(Message())
        await proxy.reply_video(video="cut.mp4")
        assert delivery.cut_replay_delivery_count() == 0
    finally:
        delivery._REPLAY_DELIVERIES.reset(counter_token)
        delivery._REPLAY_OCCURRED.reset(replay_token)


def test_delivery_policy_is_explicit_and_ordered_in_required_installer():
    quality = Path("services/shorts_factory_quality_gate.py").read_text(
        encoding="utf-8"
    )
    source_install = quality.index("if not install_cut_mode_source_policy():")
    delivery_install = quality.index(
        "if not install_cut_replay_delivery_policy():"
    )
    execution_install = quality.index(
        "if not install_shorts_factory_execution_guard():"
    )

    assert source_install < delivery_install < execution_install

    policy = Path("services/cut_replay_delivery_policy.py").read_text(
        encoding="utf-8"
    )
    assert "source_policy._same_file_path = same_replay_audio_path" in policy
    assert "if delivered <= 0:" in policy
    assert "return False" in policy
    assert "only Telegram-accepted" in policy
    assert "\ninstall_cut_replay_delivery_policy()\n" not in policy
