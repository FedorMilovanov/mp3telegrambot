from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_supplemental_health_requires_full_repair_chain() -> None:
    facade = (ROOT / "handlers" / "dub_health" / "__init__.py").read_text(
        encoding="utf-8"
    )
    required = (
        '"strict-repair-request"',
        "def _validate_repair_request(",
        "def _validated_sha256(",
        "изменился после создания repair request",
        "manifest.audio_repairs должен быть списком",
        "_legacy._checkpoint_ready = _checkpoint_ready",
        "_legacy.legacy_repair._load_segments = _load_segments",
        '"serialized-repair-handler"',
        "_DUBFIX_LOCK = asyncio.Lock()",
        "async with _DUBFIX_LOCK",
        '"transactional-repair-preprocess"',
        "strict_core._mark_and_validate_segments(",
        "allow_nan=False",
    )
    for item in required:
        assert item in facade


def test_supplemental_health_requires_source_and_segment_identity() -> None:
    facade = (ROOT / "handlers" / "dub_health" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert '"canonical-source-identity"' in facade
    assert "Project request и скачиваемый YouTube-ролик имеют разные video ID" in facade
    assert "clean_source_download._url_video_id(raw)" in facade
    assert '"strict-segment-preflight"' in facade
    assert "_legacy._mark_and_validate_segments = _mark_and_validate_segments" in facade


def test_supplemental_health_is_composed_with_base_health() -> None:
    facade = (ROOT / "handlers" / "dub_health" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "base_ok, base_detail = _legacy_quality_contract(repo)" in facade
    assert "supplemental_ok, supplemental_detail = _supplemental_quality_contract(repo)" in facade
    assert "bool(base_ok and supplemental_ok)" in facade
    assert "_legacy._quality_contract = _quality_contract" in facade
