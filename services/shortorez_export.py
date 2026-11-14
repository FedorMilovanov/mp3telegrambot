"""Offline source-bound SHORTOREZ export for reviewed 3–5 minute Russian passages.

This is a user-invoked local file exporter, NOT a Telegram bot mode, AI scout,
provider publisher, production worker, or release approval. It reuses the
existing vertical Shorts renderer and media probe; original Factory settings
and delivery behavior are unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from services.media_delivery_probe import MediaProbe, media_probe_is_deliverable, probe_media_async

PILOT_PROJECT = "lord-god-strength"
PILOT_CHANNEL = "UCeSJsC6go2c9pdJCuUI1BYA"
# The existing Factory accepts short <=177 s and long >=300 s.
GAP_MIN_MS_EXCLUSIVE = 177_000
GAP_MAX_MS_EXCLUSIVE = 300_000
_RENDER_DRIFT_MS = 1_500
_VIDEO_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")

Renderer = Callable[..., Awaitable[bool]]
Prober = Callable[[Path], Awaitable[MediaProbe | None]]


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


def _validate_request(
    youtube_id: str,
    source_sha256: str,
    start_ms: int,
    end_ms: int,
) -> None:
    if not isinstance(youtube_id, str) or not _VIDEO_ID_PATTERN.fullmatch(youtube_id):
        raise ValueError("An exact YouTube video ID is required")
    if not isinstance(source_sha256, str) or not _DIGEST_PATTERN.fullmatch(source_sha256):
        raise ValueError("Source SHA256 must be sha256: plus 64 lowercase hex digits")
    if type(start_ms) is not int or type(end_ms) is not int or start_ms < 0:
        raise ValueError("Reviewed millisecond boundaries must be nonnegative integers")
    if not GAP_MIN_MS_EXCLUSIVE < end_ms - start_ms < GAP_MAX_MS_EXCLUSIVE:
        raise ValueError("This exporter handles only the Factory 177–300 second gap")


def _verify_boundary_record(
    record: Any,
    *,
    youtube_id: str,
    source_sha256: str,
    start_ms: int,
    end_ms: int,
) -> dict[str, str]:
    if not isinstance(record, Mapping) or record.get("schema_version") != 1:
        raise ValueError("A version 1 reviewer boundary record is required")
    identity = {
        "project_key": PILOT_PROJECT,
        "source_channel_id": PILOT_CHANNEL,
        "source_video_id": youtube_id,
        "source_sha256": source_sha256,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "source_kind": "ru_postmix",
    }
    for key, expected in identity.items():
        actual = record.get(key)
        if type(actual) is not type(expected) or actual != expected:
            raise ValueError(f"Boundary evidence disagrees on {key}")
    review = record.get("boundary_review")
    if not isinstance(review, Mapping) or review.get("status") != "heard_on_ru_postmix":
        raise ValueError("No recorded RU postmix boundary-listening review")
    clean: dict[str, str] = {}
    for key in ("reviewer", "first_heard_words", "last_heard_words"):
        val = review.get(key)
        if not isinstance(val, str) or not val.strip() or len(val) > 350:
            raise ValueError(f"Boundary review must record nonempty {key}")
        clean[key] = val.strip()
    return clean


async def export_gap_interval(
    *,
    source_path: Path,
    output_dir: Path,
    evidence_path: Path,
    youtube_id: str,
    source_sha256: str,
    start_ms: int,
    end_ms: int,
    render: Renderer | None = None,
    probe: Prober | None = None,
) -> tuple[Path, Path]:
    """Export an isolated 9:16 base MP4 + provider-inert manifest.

    The boundary record is a provenance assertion from a reviewer; code cannot
    *prove* that the first/last Russian syllables were heard. After export, the
    rendered final MP4 must still be listened to and its subtitle treatment
    reviewed before separate authorization of any provider writer.
    """
    _validate_request(youtube_id, source_sha256, start_ms, end_ms)
    source = Path(source_path).resolve(strict=True)
    if not source.is_file():
        raise ValueError("Source must be an existing local regular MP4")
    if source.suffix.lower() != ".mp4":
        raise ValueError("Source postmix must be an MP4")
    actual_source_sha256 = _sha256(source)
    if actual_source_sha256 != source_sha256:
        raise ValueError("Source bytes differ from the reviewed SHA256")
    record = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    edge_review = _verify_boundary_record(
        record,
        youtube_id=youtube_id,
        source_sha256=source_sha256,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    actual_probe = probe or probe_media_async
    source_probe = await actual_probe(source)
    if not media_probe_is_deliverable(source_probe):
        raise ValueError("Owner postmix file lacks proven audio/video")
    assert source_probe is not None
    if end_ms > round(source_probe.duration * 1000) + 100:
        raise ValueError("Reviewed interval exceeds the real source duration")

    root = Path(output_dir).resolve()
    # Prevent accidental use of the input filename as an output artifact.
    stem = f"shortorez_{youtube_id}_{start_ms}-{end_ms}_{source_sha256[7:19]}"
    final_mp4 = root / (stem + "_vertical.mp4")
    final_json = root / (stem + "_handoff.json")
    if final_mp4 == source:
        raise ValueError("Refusing to overwrite the source postmix")
    if final_mp4.exists() or final_json.exists():
        raise FileExistsError("Existing SHORTOREZ media or manifest needs reconciliation")
    root.mkdir(parents=True, exist_ok=True)

    if render is None:
        # Existing owned Shorts renderer; no new FFmpeg engine. Import on demand,
        # so checking manifests never imports the bot or starts production tasks.
        from services.shorts_video import render_short_clip

        render = render_short_clip

    with tempfile.TemporaryDirectory(prefix=".shortorez-", dir=root) as temp:
        staged_mp4 = Path(temp) / "rendered.mp4"
        staged_json = Path(temp) / "handoff.json"
        success = await render(
            source,
            staged_mp4,
            start_ms / 1000.0,
            end_ms / 1000.0,
            visual_mode="full_frame_vertical",
            snap_to_silence=False,
        )
        if not success or not staged_mp4.is_file() or staged_mp4.stat().st_size <= 0:
            raise RuntimeError("Owned vertical renderer failed; no delivery artifact created")
        result_probe = await actual_probe(staged_mp4)
        if not media_probe_is_deliverable(result_probe):
            raise RuntimeError("Rendered 9:16 file lacks actual audio/video")
        assert result_probe is not None
        if (result_probe.width, result_probe.height) != (720, 1280):
            raise RuntimeError("Render is not the expected 720x1280 vertical output")
        expected_ms = end_ms - start_ms
        actual_ms = round(result_probe.duration * 1000)
        if actual_ms >= GAP_MAX_MS_EXCLUSIVE or abs(actual_ms - expected_ms) > _RENDER_DRIFT_MS:
            raise RuntimeError("Final MP4 duration differs from the reviewed complete interval")
        output_sha256 = _sha256(staged_mp4)
        manifest = {
            "schema_version": 1,
            "kind": "shortorez_review_only",
            "project_key": PILOT_PROJECT,
            "source_channel_id": PILOT_CHANNEL,
            "source_video_id": youtube_id,
            "full_video_url": f"https://www.youtube.com/watch?v={youtube_id}",
            "source_kind": "ru_postmix",
            "source_sha256": actual_source_sha256,
            "source_start_ms": start_ms,
            "source_end_ms": end_ms,
            "boundary_review": edge_review,
            "output_file": final_mp4.name,
            "output_sha256": output_sha256,
            "output_duration_ms": actual_ms,
            "output_width": result_probe.width,
            "output_height": result_probe.height,
            "speed_percent": 100,
            "render_mode": "existing_full_frame_vertical",
            "subtitles_reviewed": False,
            "final_encoded_audio_listened": False,
            "visual_safe_zones_reviewed": False,
            "rights_cleared": False,
            "release_authorized": False,
            "provider_write_performed": False,
        }
        staged_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Hardlinks inside one directory publish only fully verified local files,
        # without replacing a preexisting artifact even in a concurrent race.
        # If hardlinks are unsupported on the operator's filesystem, stop rather
        # than silently overwrite or use a second unsafe publication path.
        os.link(staged_mp4, final_mp4)
        try:
            os.link(staged_json, final_json)
        except BaseException:
            final_mp4.unlink(missing_ok=True)
            raise
    return final_mp4, final_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SHORTOREZ offline 3–5m MP4 export; no provider writes")
    parser.add_argument("--source-path", type=Path, required=True, help="Reviewed local Russian postmix MP4")
    parser.add_argument("--source-sha256", required=True, help="sha256:<64 lower-case hex>")
    parser.add_argument("--ru-boundaries-json", type=Path, required=True, help="Exact RU postmix listening record")
    parser.add_argument("--youtube-id", required=True, help="Exact original owner YouTube video ID")
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--end-ms", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="Separate local review output directory")
    args = parser.parse_args(argv)
    media, manifest = asyncio.run(
        export_gap_interval(
            source_path=args.source_path,
            output_dir=args.output_dir,
            evidence_path=args.ru_boundaries_json,
            youtube_id=args.youtube_id,
            source_sha256=args.source_sha256,
            start_ms=args.start_ms,
            end_ms=args.end_ms,
        )
    )
    print(json.dumps({
        "status": "exported_for_review",
        "media_path": str(media),
        "manifest_path": str(manifest),
        "provider_write_performed": False,
        "release_authorized": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
