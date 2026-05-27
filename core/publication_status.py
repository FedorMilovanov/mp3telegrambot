#!/usr/bin/env python3
"""Publication completeness/status helpers.

Small pure layer used by the pipeline and tests. It keeps UX/status decisions
out of the huge async pipeline and prepares the future generated_pages archive.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class PublicationStatus:
    status: str = "unknown"  # complete | partial | failed | unknown
    missing: tuple[str, ...] = field(default_factory=tuple)
    warning: str = ""

    def to_json(self) -> str:
        return json.dumps({
            "status": self.status,
            "missing": list(self.missing),
            "warning": self.warning,
        }, ensure_ascii=False)


_LABELS = {
    "synopsis": "конспект",
    "study": "разбор материала",
    "reflection": "размышление",
}


def build_publication_status(
    *,
    synopsis_url: str = "",
    study_url: str = "",
    reflection_url: str = "",
    expect_synopsis: bool = True,
    expect_study: bool = True,
    expect_reflection: bool = True,
) -> PublicationStatus:
    """Return normalized publication completeness state.

    This function intentionally knows only about the three public article-like
    pages. Legacy compact URLs may exist, but they do not make a V3 publication
    "complete" if an expected V3 page is absent.
    """
    missing: list[str] = []
    if expect_synopsis and not synopsis_url:
        missing.append("synopsis")
    if expect_study and not study_url:
        missing.append("study")
    if expect_reflection and not reflection_url:
        missing.append("reflection")

    expected_count = sum(1 for x in (expect_synopsis, expect_study, expect_reflection) if x)
    present_count = sum(1 for x in (
        bool(synopsis_url) if expect_synopsis else False,
        bool(study_url) if expect_study else False,
        bool(reflection_url) if expect_reflection else False,
    ) if x)

    if expected_count == 0:
        status = "unknown"
    elif not missing:
        status = "complete"
    elif present_count == 0:
        status = "failed"
    else:
        status = "partial"

    warning = ""
    if missing and status in {"partial", "failed"}:
        labels = [_LABELS.get(m, m) for m in missing]
        warning = "Частичный разбор: не созданы " + ", ".join(labels) + "."
        if status == "failed":
            warning = "Разбор не был опубликован полностью: не созданы " + ", ".join(labels) + "."

    return PublicationStatus(status=status, missing=tuple(missing), warning=warning)


def missing_to_json(missing: Iterable[str]) -> str:
    return json.dumps(list(missing or []), ensure_ascii=False)
