"""Cache-hit presentation, delivery, and finalization stay in historical order."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path

import pytest

from pipelines.cache_hit_delivery_stage import CacheHitDeliveryRequest, deliver_cache_hit
from pipelines.cached_presentation_stage import CachedCaption, CachedPageLinks
from pipelines.delivery_stage import DeliveryOutcome


def _request(events) -> CacheHitDeliveryRequest:
    async def send_livedub():
        events.append(("livedub",))
        return False

    def cleanup(media_id):
        events.append(("cleanup", media_id))

    return CacheHitDeliveryRequest(
        links=CachedPageLinks(
            synopsis_url="synopsis",
            quotes_url="quotes",
            questions_url="questions",
            terms_url="terms",
            study_url="study",
            reflection_url="reflection",
        ),
        ai_data={"format": "sermon", "real_title": "Cached title"},
        full_title="Full title",
        channel_name="Channel",
        title="Title",
        performer="Performer",
        duration=600,
        file_size_mb=12.5,
        bitrate="128k",
        source_url="https://youtube.com/watch?v=media",
        cached_rutube_url="cached-rutube",
        cached_vk_url="cached-vk",
        update=object(),
        context=object(),
        audio_delivery=object(),
        mp3_path=Path("downloads/media.mp3"),
        media_id="media",
        download_dir=Path("downloads"),
        thumbs_dir=Path("thumbs"),
        thumbnail=object(),
        cached_file_id="cached-file-id",
        send_livedub=send_livedub,
        cleanup=cleanup,
    )


def _dependencies(events, *, fail_at=""):
    async def prepare(request):
        events.append(("presentation", request))
        if fail_at == "presentation":
            raise RuntimeError("presentation failed")
        return CachedCaption(caption="caption", full_caption="full caption")

    async def deliver(request):
        events.append(("delivery", request))
        if fail_at == "delivery":
            raise RuntimeError("delivery failed")
        return DeliveryOutcome(
            chapters_embedded=False,
            audio_message=None,
            full_caption_sent=True,
            pdf_status="skipped",
            quiz_sent=0,
        )

    return {"prepare": prepare, "deliver": deliver}


def test_cache_hit_success_preserves_requests_order_and_cleanup(caplog):
    events = []
    request = _request(events)

    with caplog.at_level(logging.INFO):
        result = asyncio.run(
            deliver_cache_hit(
                request,
                **_dependencies(events),
            )
        )

    assert result is True
    assert [event[0] for event in events] == [
        "presentation",
        "delivery",
        "livedub",
        "cleanup",
    ]
    assert events[-1] == ("cleanup", "media")
    assert "Кэш hit: media" in caplog.text

    presented = events[0][1]
    assert presented.links is request.links
    assert presented.ai_data is request.ai_data
    assert presented.full_title == "Full title"
    assert presented.channel_name == "Channel"
    assert presented.title == "Title"
    assert presented.performer == "Performer"
    assert presented.duration == 600
    assert presented.file_size_mb == 12.5
    assert presented.bitrate == "128k"
    assert presented.source_url == request.source_url
    assert presented.cached_rutube_url == "cached-rutube"
    assert presented.cached_vk_url == "cached-vk"

    delivered = events[1][1]
    assert delivered.update is request.update
    assert delivered.context is request.context
    assert delivered.audio_delivery is request.audio_delivery
    assert delivered.mp3_path == request.mp3_path
    assert delivered.media_id == "media"
    assert delivered.source_url == request.source_url
    assert delivered.title == "Title"
    assert delivered.performer == "Performer"
    assert delivered.duration == 600
    assert delivered.file_size_mb == 12.5
    assert delivered.caption == "caption"
    assert delivered.full_caption == "full caption"
    assert delivered.download_dir == request.download_dir
    assert delivered.thumbs_dir == request.thumbs_dir
    assert delivered.ai_data is request.ai_data
    assert delivered.thumbnail is request.thumbnail
    assert delivered.synopsis_url == "synopsis"
    assert delivered.study_url == "study"
    assert delivered.reflection_url == "reflection"
    assert delivered.terms_url == "terms"
    assert delivered.cached_file_id == "cached-file-id"
    assert delivered.cache_hit is False


@pytest.mark.parametrize(
    ("fail_at", "expected_events"),
    [
        ("presentation", ["presentation"]),
        ("delivery", ["presentation", "delivery"]),
        ("livedub", ["presentation", "delivery", "livedub"]),
    ],
)
def test_failures_propagate_without_success_cleanup(fail_at, expected_events):
    events = []
    request = _request(events)
    if fail_at == "livedub":

        async def broken_livedub():
            events.append(("livedub",))
            raise RuntimeError("livedub failed")

        request = replace(request, send_livedub=broken_livedub)

    with pytest.raises(RuntimeError, match=f"{fail_at} failed"):
        asyncio.run(
            deliver_cache_hit(
                request,
                **_dependencies(events, fail_at=fail_at),
            )
        )

    assert [event[0] for event in events] == expected_events
    assert all(event[0] != "cleanup" for event in events)
