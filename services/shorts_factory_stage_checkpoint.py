"""Durable, evidence-bound checkpoints for strict three-pass Factory review.

Only pre-delivery model results are cached. This module never schedules a retry
or repeats a Telegram send; user-initiated retries may reuse completed passes.
A checkpoint is rejected if the decoded audio, model, prompt, schema, quality
code, or retention policy has changed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.shorts_factory_retry_cache import FACTORY_CACHE_DIR, cache_ttl_seconds

logger = logging.getLogger(__name__)

STAGE_CHECKPOINT_DIR = FACTORY_CACHE_DIR / "high_pass_checkpoints"
_STAGE_POLICY = "factory-high-verified-pre-delivery-v1"
_ALLOWED_STAGES = frozenset({"scout", "judge", "audit"})
_MAX_CHECKPOINT_BYTES = 2 * 1024 * 1024
_MAX_CHECKPOINT_FILES = 24
_DEPENDENCY_FILES = (
    "shorts_factory_candidates.py",
    "shorts_factory_source.py",
    "shorts_factory_quality_gate.py",
    "shorts_factory_capacity_runtime.py",
    "../core/globals.py",
    "../core/model_catalog.py",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dependency_sha256() -> str:
    base = Path(__file__).resolve().parent
    return _sha256(_canonical({name: _file_sha256(base / name) for name in _DEPENDENCY_FILES}))


def _valid_structure(payload: Any, *, stage: str, duration: int) -> bool:
    """Check source-aligned shape; this is not a replacement for final quality QA."""
    if not isinstance(payload, dict):
        return False
    metadata = payload.get("metadata")
    shorts = payload.get("shorts_candidates")
    longs = payload.get("long_candidates")
    if not isinstance(metadata, dict) or not isinstance(shorts, list) or not isinstance(longs, list):
        return False
    if len(shorts) > 60 or len(longs) > 60:
        return False
    for key in ("language", "format", "title_ru", "author_ru"):
        if not isinstance(metadata.get(key), str):
            return False
    for candidate in shorts + longs:
        if not isinstance(candidate, dict):
            return False
        for key in ("start_seconds", "end_seconds", "quality_score"):
            value = candidate.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                return False
        if (
            candidate["start_seconds"] < 0
            or candidate["end_seconds"] <= candidate["start_seconds"]
            or (duration > 0 and candidate["end_seconds"] > duration)
        ):
            return False
        if not isinstance(candidate.get("title_ru"), str) or not isinstance(candidate.get("reason_ru"), str):
            return False
        if candidate.get("boundary_verified") not in (True, False):
            return False
        if stage == "audit" and candidate["boundary_verified"] is not True:
            return False
    return True


def _safe_prune(directory: Path, *, now: float) -> None:
    """Bound retention and disk use. Never inspect outside the checkpoint folder."""
    if not directory.exists():
        return
    surviving: list[tuple[float, Path]] = []
    for path in directory.glob("*.json"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            if stat.st_size > _MAX_CHECKPOINT_BYTES or now - stat.st_mtime > cache_ttl_seconds():
                path.unlink(missing_ok=True)
            else:
                surviving.append((stat.st_mtime, path))
        except OSError:
            logger.debug("Factory stage checkpoint prune failed for one file", exc_info=True)
    for _, path in sorted(surviving, reverse=True)[_MAX_CHECKPOINT_FILES:]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.debug("Factory stage checkpoint retention cleanup failed", exc_info=True)


@dataclass(frozen=True)
class StageCheckpointStore:
    audio_sha256: str
    duration: int
    model: str
    schema_sha256: str
    dependency_sha256: str
    directory: Path

    @classmethod
    async def from_audio(
        cls,
        audio_path: Path,
        *,
        duration: int,
        model: str,
        schema: dict[str, Any],
    ) -> StageCheckpointStore:
        audio_hash, dependencies = await asyncio.gather(
            asyncio.to_thread(_file_sha256, Path(audio_path)),
            asyncio.to_thread(_dependency_sha256),
        )
        return cls(
            audio_sha256=audio_hash,
            duration=int(duration),
            model=str(model),
            schema_sha256=_sha256(_canonical(schema)),
            dependency_sha256=dependencies,
            directory=STAGE_CHECKPOINT_DIR,
        )

    def _identity(self, stage: str, prompt: str) -> dict[str, Any]:
        if stage not in _ALLOWED_STAGES:
            raise ValueError("invalid Factory checkpoint stage")
        return {
            "policy": _STAGE_POLICY,
            "audio_sha256": self.audio_sha256,
            "duration": self.duration,
            "model": self.model,
            "thinking_level": "high",
            "schema_thinking_enabled": (os.getenv("GEMINI_SCHEMA_THINKING", "1") or "1").strip().lower()
            not in {"0", "false", "no", "off"},
            "stage": stage,
            "prompt_sha256": _sha256(prompt.encode("utf-8")),
            "schema_sha256": self.schema_sha256,
            "dependency_sha256": self.dependency_sha256,
        }

    def _path(self, stage: str, prompt: str) -> Path:
        return self.directory / f"{_sha256(_canonical(self._identity(stage, prompt)))}.json"

    def load(self, stage: str, prompt: str) -> dict[str, Any] | None:
        path = self._path(stage, prompt)
        try:
            raw = path.read_bytes()
            if len(raw) > _MAX_CHECKPOINT_BYTES:
                return None
            item = json.loads(raw)
            if not isinstance(item, dict) or item.get("identity") != self._identity(stage, prompt):
                return None
            created = item.get("created_at")
            if not isinstance(created, (int, float)) or not math.isfinite(created):
                return None
            if created <= 0 or time.time() < created or time.time() - created > cache_ttl_seconds():
                return None
            payload = item.get("payload")
            if not _valid_structure(payload, stage=stage, duration=self.duration):
                return None
            if item.get("payload_sha256") != _sha256(_canonical(payload)):
                return None
            logger.info("Factory HIGH pass checkpoint hit stage=%s", stage)
            return payload
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def save(self, stage: str, prompt: str, payload: dict[str, Any]) -> None:
        if not _valid_structure(payload, stage=stage, duration=self.duration):
            logger.info("Factory HIGH pass stage=%s failed checkpoint-shape validation; not persisted", stage)
            return
        item = {
            "identity": self._identity(stage, prompt),
            "created_at": time.time(),
            "payload": payload,
            "payload_sha256": _sha256(_canonical(payload)),
        }
        encoded = _canonical(item)
        if len(encoded) > _MAX_CHECKPOINT_BYTES:
            logger.info("Factory HIGH pass stage=%s exceeds checkpoint size limit; not persisted", stage)
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".factory-pass-", suffix=".tmp", dir=self.directory)
        path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(encoded)
                out.flush()
                os.fsync(out.fileno())
            os.replace(path, self._path(stage, prompt))
            logger.info("Factory HIGH pass checkpoint written stage=%s", stage)
            _safe_prune(self.directory, now=time.time())
        finally:
            path.unlink(missing_ok=True)


__all__ = ["StageCheckpointStore", "STAGE_CHECKPOINT_DIR"]
