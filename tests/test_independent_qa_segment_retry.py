#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.dub_worker_release import (
    INDEPENDENT_QA_RECOVERY_POLICY,
    WORKER_RUNTIME,
)
from tools.voxcpm2 import independent_qa_retry as recovery


_FAILURE = (
    "RuntimeError: Чистый direct renderer не прошёл независимый QA после одного "
    "прицельного повтора. Сегменты: [7]. Причины: #7: ASR recall=0.3333, "
    "услышано=«25 стих», стык onset=2230.0ms tail=230.0ms"
)


def _project(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "project"
    audio = root / "audio"
    audio.mkdir(parents=True)
    segments = root / "segments_ru_final.json"
    segments.write_text(
        json.dumps(
            [
                {"id": 1, "text": "Первая принятая реплика."},
                {"id": 7, "text": "Двадцать пятый стих и полная мысль."},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (audio / "piper_ru_timeline.clean_qa.json").write_text(
        json.dumps(
            {
                "passed": False,
                "failed_segment_ids": [7],
                "segments": [
                    {"id": 1, "passed": True},
                    {
                        "id": 7,
                        "passed": False,
                        "semantic": {
                            "passed": False,
                            "token_recall": 0.3333,
                            "heard": "25 стих",
                        },
                        "timing": {
                            "passed": False,
                            "onset_ms": 2230.0,
                            "trailing_ms": 230.0,
                        },
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root, segments


def test_recovery_advances_seed_pairs_and_retargets_only_failed_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, segments = _project(tmp_path)
    calls: list[int] = []
    retargets: list[dict[str, object]] = []

    monkeypatch.setattr(
        recovery,
        "_runtime_settings",
        lambda request, duration: {
            "base_seed": int(request["base_seed"]),
            "video_id": "piper",
        },
    )
    monkeypatch.setattr(recovery, "_retry_seed_offset", lambda: 10)
    monkeypatch.setattr(recovery, "_log", lambda message: None)

    def fake_retarget(
        work_dir: Path,
        *,
        good_ids: set[int],
        failed_ids: list[int],
        new_base_seed: int,
    ) -> None:
        retargets.append(
            {
                "work_dir": work_dir,
                "good_ids": set(good_ids),
                "failed_ids": list(failed_ids),
                "new_base_seed": int(new_base_seed),
            }
        )

    monkeypatch.setattr(recovery, "_retarget_checkpoints", fake_retarget)

    def original(*, request: dict[str, object], **kwargs: object) -> str:
        calls.append(int(request["base_seed"]))
        if len(calls) < 3:
            raise RuntimeError(_FAILURE)
        return "accepted"

    result = recovery._run_with_recovery(
        original,
        root=root,
        request={"base_seed": 100},
        duration=30.0,
        segments_json=segments,
    )

    assert result == "accepted"
    assert calls == [100, 120, 140]
    assert [item["new_base_seed"] for item in retargets] == [120, 140]
    assert all(item["good_ids"] == {1} for item in retargets)
    assert all(item["failed_ids"] == [7] for item in retargets)
    assert all(item["work_dir"] == root / "segment_work" for item in retargets)


def test_recovery_is_fail_closed_without_authoritative_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    segments = root / "segments_ru_final.json"
    segments.write_text('[{"id": 7}]', encoding="utf-8")
    monkeypatch.setattr(
        recovery,
        "_runtime_settings",
        lambda request, duration: {"base_seed": 100, "video_id": "missing"},
    )

    def original(**kwargs: object) -> None:
        raise RuntimeError(_FAILURE)

    with pytest.raises(RuntimeError, match="независимый QA"):
        recovery._run_with_recovery(
            original,
            root=root,
            request={"base_seed": 100},
            duration=30.0,
            segments_json=segments,
        )


