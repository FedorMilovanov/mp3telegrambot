"""Build and deliver the cache-hit result after thumbnail and audio succeed."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from pipelines.cached_presentation_stage import (
    CachedCaption,
    CachedPageLinks,
    CachedPresentationRequest,
)
from pipelines.delivery_stage import DeliveryRequest, DeliveryOutcome

logger = logging.getLogger(__name__)

PresentationFn = Callable[[CachedPresentationRequest], Awaitable[CachedCaption]]
DeliveryFn = Callable[[DeliveryRequest], Awaitable[DeliveryOutcome]]
LiveDubFn = Callable[[], Awaitable[bool]]
CleanupFn = Callable[[str], Any]


@dataclass(frozen=True)
class CacheHitDeliveryRequest:
    """Shared values for cached presentation, delivery, and finalization."""

    links: CachedPageLinks
    ai_data: dict[str, Any]
    full_title: str
    channel_name: str
    title: str
    performer: str
    duration: int
    file_size_mb: float
    bitrate: str
    source_url: str
    cached_rutube_url: str
    cached_vk_url: str
    update: Any
    context: Any
    audio_delivery: Any
    mp3_path: Path
    media_id: str
    download_dir: Path
    thumbs_dir: Path
    thumbnail: Any
    cached_file_id: str
    send_livedub: LiveDubFn
    cleanup: CleanupFn


async def deliver_cache_hit(
    request: CacheHitDeliveryRequest,
    *,
    prepare: PresentationFn,
    deliver: DeliveryFn,
) -> bool:
    """Preserve cache-hit presentation, delivery, LiveDub, and cleanup order."""
    cached_caption = await prepare(
        CachedPresentationRequest(
            links=request.links,
            ai_data=request.ai_data,
            full_title=request.full_title,
            channel_name=request.channel_name,
            title=request.title,
            performer=request.performer,
            duration=request.duration,
            file_size_mb=request.file_size_mb,
            bitrate=request.bitrate,
            source_url=request.source_url,
            cached_rutube_url=request.cached_rutube_url,
            cached_vk_url=request.cached_vk_url,
        )
    )
    await deliver(
        DeliveryRequest(
            update=request.update,
            context=request.context,
            audio_delivery=request.audio_delivery,
            mp3_path=request.mp3_path,
            media_id=request.media_id,
            source_url=request.source_url,
            title=request.title,
            performer=request.performer,
            duration=request.duration,
            file_size_mb=request.file_size_mb,
            caption=cached_caption.caption,
            full_caption=cached_caption.full_caption,
            download_dir=request.download_dir,
            thumbs_dir=request.thumbs_dir,
            ai_data=request.ai_data,
            thumbnail=request.thumbnail,
            synopsis_url=request.links.synopsis_url or "",
            study_url=request.links.study_url,
            reflection_url=request.links.reflection_url,
            terms_url=request.links.terms_url,
            cached_file_id=request.cached_file_id,
        )
    )
    # The cached path must finish even without LiveDub or Telegram context.
    await request.send_livedub()
    request.cleanup(request.media_id)
    logger.info(f"Кэш hit: {request.media_id}")
    return True
