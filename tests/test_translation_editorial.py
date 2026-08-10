from __future__ import annotations

import inspect
import json
import zipfile
from pathlib import Path

import services.translation_editorial as editorial
from services.translation_editorial import (
    PACK_SCHEMA_NAME,
    REVIEW_SCHEMA_NAME,
    build_drop_filter,
    build_review_pack,
    collect_executable_repairs,
    find_donor_cues,
    load_pack_manifest,
    parse_srt_text,
    remap_after_drops,
    validate_review_document,
)


def _write_srt(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    blocks = []
    for index, (start, end, text) in enumerate(rows, 1):
        blocks.append(f"{index}\n{start} --> {end}\n{text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def _pack(tmp_path: Path, *, russian_text: str = "Вера отдельно от работы.") -> Path:
    video = tmp_path / "translated.mp4"
    video.write_bytes(b"video-bytes-for-hash")
    original = _write_srt(
        tmp_path / "en.srt",
        [
            ("00:00:01,000", "00:00:03,000", "Faith apart from works."),
            ("00:00:10,000", "00:00:12,000", "Their nakedness was exposed."),
        ],
    )
    russian = _write_srt(
        tmp_path / "ru.srt",
        [
            ("00:00:01,100", "00:00:03,100", russian_text),
            ("00:00:10,100", "00:00:12,100", "Их нагота была открыта."),
            ("00:00:20,000", "00:00:21,000", "Дела не спасают."),
        ],
    )
    words = tmp_path / "words.json"
    words.write_text(
        json.dumps(
            [
                {"word": "дела", "start": 20.0, "end": 20.3},
                {"word": "не", "start": 20.3, "end": 20.4},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return build_review_pack(
        output_dir=tmp_path,
        media_id="abc123",
        source_url="https://example.invalid/watch?v=abc123",
        title="Test Sermon",
        performer="Test Preacher",
        duration=60.0,
        source_video_path=video,
        original_srt_path=original,
        russian_whisper_srt_path=russian,
        russian_words_path=words,
        shorts_candidates=[
            {"title": "Short", "start_seconds": 0.0, "end_seconds": 30.0}
        ],
        long_candidates=[
            {"title": "Long", "start_seconds": 0.0, "end_seconds": 60.0}
        ],
        timeline_metadata={
            "original_srt": "source_timeline",
            "russian_whisper": "translated_video_timeline",
            "russian_delay_seconds": 0.6,
        },
    )


def _keep_candidates() -> list[dict]:
    return [
        {"candidate_id": "short:1", "verdict": "keep", "issues": []},
        {"candidate_id": "long:1", "verdict": "keep", "issues": []},
    ]


def test_parse_srt_supports_long_hours_and_multiline_text() -> None:
    cues = parse_srt_text(
        "1\n02:03:04,125 --> 02:03:06,500\nFirst line\nSecond line\n\n"
    )
    assert len(cues) == 1
    assert cues[0].start == 7384.125
    assert cues[0].end == 7386.5
    assert cues[0].text == "First line Second line"


def test_review_pack_is_small_verified_exchange_without_video_bytes(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    manifest = load_pack_manifest(pack)

    assert manifest["schema_name"] == PACK_SCHEMA_NAME
    assert manifest["schema_version"] == 1
    assert manifest["review_pack_id"].startswith("sha256:")
    assert manifest["candidates"]["shorts"][0]["candidate_id"] == "short:1"
    assert manifest["candidates"]["long_clips"][0]["candidate_id"] == "long:1"
    assert manifest["timeline"]["russian_delay_seconds"] == 0.6
    assert manifest["review_contract"]["automatically_executable_actions"] == [
        "drop_span",
        "mute_span",
    ]

    with zipfile.ZipFile(pack, "r") as archive:
        names = set(archive.namelist())
    assert "manifest.json" in names
    assert "original.srt" in names
    assert "russian_whisper.srt" in names
    assert "russian_whisper_words.json" in names
    assert "candidates.json" in names
    assert "translated.mp4" not in names
    assert manifest["review_pack_id"][7:19] in pack.name


def test_review_pack_rerun_preserves_distinct_immutable_versions(tmp_path: Path) -> None:
    first = _pack(tmp_path, russian_text="Вера отдельно от работы.")
    first_bytes = first.read_bytes()
    second = _pack(tmp_path, russian_text="Вера отдельно от дел.")

    assert first != second
    assert first.exists() and second.exists()
    assert first.read_bytes() == first_bytes
    assert load_pack_manifest(first)["review_pack_id"] != load_pack_manifest(second)["review_pack_id"]


def test_pack_loader_rejects_tampered_transcript_under_old_manifest(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(pack, "r") as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "russian_whisper.srt":
                payload += b"\nTAMPERED\n"
            target.writestr(info, payload)

    try:
        load_pack_manifest(tampered)
    except ValueError as exc:
        assert "transcript" in str(exc)
    else:
        raise AssertionError("tampered review evidence must fail closed")


def test_same_voice_donor_search_is_grounded_and_uses_phrase_boundaries(tmp_path: Path) -> None:
    russian = _write_srt(
        tmp_path / "ru.srt",
        [
            ("00:00:01,000", "00:00:02,000", "Эти дела бесполезны."),
            ("00:00:05,000", "00:00:06,000", "Нам нужно сделать вывод."),
            ("00:00:10,000", "00:00:11,000", "Но добрые дела следуют за верой."),
            ("00:00:20,000", "00:00:21,000", "Ещё одна мысль."),
        ],
    )
    donors = find_donor_cues(
        russian,
        "дела",
        exclude_start=0.5,
        exclude_end=3.0,
    )
    assert donors == [
        {"start": 10.0, "end": 11.0, "text": "Но добрые дела следуют за верой."}
    ]


def test_review_validation_binds_exact_pack_candidate_ids_and_candidate_spans(tmp_path: Path) -> None:
    pack = _pack(tmp_path)
    manifest = load_pack_manifest(pack)
    candidate_issue = {
        "start_seconds": 1.3,
        "end_seconds": 1.65,
        "severity": "major",
        "observed_ru": "работы",
        "source_meaning": "дел",
        "action": {"type": "drop_span"},
    }
    review = {
        "schema_name": REVIEW_SCHEMA_NAME,
        "schema_version": 1,
        "review_pack_id": manifest["review_pack_id"],
        "reviewer": "chatgpt",
        "full_sermon": {
            "verdict": "repair",
            "issues": [dict(candidate_issue)],
        },
        "candidate_reviews": [
            {"candidate_id": "short:1", "verdict": "repair", "issues": [dict(candidate_issue)]},
            {"candidate_id": "long:1", "verdict": "keep", "issues": []},
        ],
    }
    assert validate_review_document(review, manifest) == []

    review["review_pack_id"] = "sha256:wrong"
    review["candidate_reviews"][0]["candidate_id"] = "short:999"
    errors = validate_review_document(review, manifest)
    assert "review_pack_id does not match the exact pack" in errors
    assert "unknown candidate_id: short:999" in errors


def test_review_requires_all_candidates_and_verdict_issue_consistency(tmp_path: Path) -> None:
    manifest = load_pack_manifest(_pack(tmp_path))
    review = {
        "schema_name": REVIEW_SCHEMA_NAME,
        "schema_version": 1,
        "review_pack_id": manifest["review_pack_id"],
        "full_sermon": {
            "verdict": "keep",
            "issues": [
                {
                    "start_seconds": 1.0,
                    "end_seconds": 1.2,
                    "severity": "major",
                    "action": {"type": "drop_span"},
                }
            ],
        },
        "candidate_reviews": [],
    }
    errors = validate_review_document(review, manifest)

    assert any("keep verdict cannot carry" in item for item in errors)
    assert any("missing candidate reviews" in item for item in errors)

    review["full_sermon"] = {"verdict": "repair", "issues": []}
    review["candidate_reviews"] = _keep_candidates()
    errors = validate_review_document(review, manifest)
    assert any("repair verdict requires at least one issue" in item for item in errors)


def test_candidate_issue_cannot_point_elsewhere_in_sermon(tmp_path: Path) -> None:
    manifest = load_pack_manifest(_pack(tmp_path))
    review = {
        "schema_name": REVIEW_SCHEMA_NAME,
        "schema_version": 1,
        "review_pack_id": manifest["review_pack_id"],
        "full_sermon": {"verdict": "keep", "issues": []},
        "candidate_reviews": [
            {
                "candidate_id": "short:1",
                "verdict": "repair",
                "issues": [
                    {
                        "start_seconds": 40.0,
                        "end_seconds": 41.0,
                        "severity": "major",
                        "action": {"type": "drop_span"},
                    }
                ],
            },
            {"candidate_id": "long:1", "verdict": "keep", "issues": []},
        ],
    }

    assert any(
        "issue span lies outside reviewed candidate" in item
        for item in validate_review_document(review, manifest)
    )


def test_borrow_span_is_valid_review_intent_but_not_auto_executable(tmp_path: Path) -> None:
    manifest = load_pack_manifest(_pack(tmp_path))
    review = {
        "schema_name": REVIEW_SCHEMA_NAME,
        "schema_version": 1,
        "review_pack_id": manifest["review_pack_id"],
        "full_sermon": {
            "verdict": "repair",
            "issues": [
                {
                    "start_seconds": 1.0,
                    "end_seconds": 1.4,
                    "severity": "major",
                    "action": {
                        "type": "borrow_span",
                        "donor_start_seconds": 20.0,
                        "donor_end_seconds": 20.4,
                        "expected_text": "дела",
                    },
                }
            ],
        },
        "candidate_reviews": _keep_candidates(),
    }
    assert validate_review_document(review, manifest) == []
    assert collect_executable_repairs(review) == []


def test_borrow_span_overlap_is_rejected(tmp_path: Path) -> None:
    manifest = load_pack_manifest(_pack(tmp_path))
    review = {
        "schema_name": REVIEW_SCHEMA_NAME,
        "schema_version": 1,
        "review_pack_id": manifest["review_pack_id"],
        "full_sermon": {
            "verdict": "repair",
            "issues": [
                {
                    "start_seconds": 10.0,
                    "end_seconds": 11.0,
                    "severity": "major",
                    "action": {
                        "type": "borrow_span",
                        "donor_start_seconds": 10.5,
                        "donor_end_seconds": 11.5,
                    },
                }
            ],
        },
        "candidate_reviews": _keep_candidates(),
    }
    assert any(
        "donor span overlaps replacement target" in item
        for item in validate_review_document(review, manifest)
    )


def test_drop_filter_merges_overlaps_and_remaps_following_time() -> None:
    drops = [(2.0, 3.0), (2.5, 3.5), (5.0, 5.5)]
    filter_complex, keep_count = build_drop_filter(8.0, drops)

    assert keep_count == 3
    assert "trim=start=0.000:end=2.000" in filter_complex
    assert "trim=start=3.500:end=5.000" in filter_complex
    assert "trim=start=5.500:end=8.000" in filter_complex
    assert "concat=n=3:v=1:a=1[outv][outa]" in filter_complex
    merged = [(2.0, 3.5), (5.0, 5.5)]
    assert remap_after_drops(1.0, merged) == 1.0
    assert remap_after_drops(4.0, merged) == 2.5
    assert remap_after_drops(6.0, merged) == 4.0


def test_collect_executable_repairs_uses_full_sermon_only() -> None:
    review = {
        "full_sermon": {
            "verdict": "repair",
            "issues": [
                {
                    "start_seconds": 4.0,
                    "end_seconds": 4.2,
                    "severity": "minor",
                    "action": {"type": "drop_span"},
                },
                {
                    "start_seconds": 8.0,
                    "end_seconds": 8.4,
                    "severity": "minor",
                    "action": {"type": "mute_span"},
                },
                {
                    "start_seconds": 12.0,
                    "end_seconds": 12.3,
                    "severity": "major",
                    "action": {
                        "type": "borrow_span",
                        "donor_start_seconds": 20.0,
                        "donor_end_seconds": 20.3,
                    },
                },
            ],
        },
        "candidate_reviews": [
            {
                "candidate_id": "short:1",
                "verdict": "repair",
                "issues": [
                    {
                        "start_seconds": 30.0,
                        "end_seconds": 31.0,
                        "severity": "minor",
                        "action": {"type": "drop_span"},
                    }
                ],
            }
        ],
    }
    assert collect_executable_repairs(review) == [
        {"start_seconds": 4.0, "end_seconds": 4.2, "type": "drop_span"},
        {"start_seconds": 8.0, "end_seconds": 8.4, "type": "mute_span"},
    ]


def test_safe_repair_guards_source_and_existing_output_before_ffmpeg() -> None:
    source = inspect.getsource(editorial.apply_safe_repairs)
    same_path_guard = source.index("refusing to overwrite source video")
    existing_output_guard = source.index("refusing to overwrite existing repair output")
    ffmpeg_lookup = source.index('shutil.which("ffmpeg")')

    assert same_path_guard < ffmpeg_lookup
    assert existing_output_guard < ffmpeg_lookup
    assert '"-n"' in source
    assert '"-y"' not in source
    assert "media_probe_is_deliverable" in source


def test_whisper_evidence_polisher_only_normalizes_whitespace() -> None:
    assert editorial._heard_text("  раБОТа   дела  ") == "раБОТа дела"
