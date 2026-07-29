#!/usr/bin/env python3
"""One Russian title policy for Shorts, Clips, LiveDub and Dub Studio.

Significant words use project Title Case. Russian conjunctions, particles and
prepositions stay lowercase everywhere except at the beginning. The policy is
also applied when old Dub Studio rows, progress events and manifest filenames
are displayed, so historical projects do not need another render merely to fix
typography.
"""
from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

from core.person_names import normalize_person_names

_INSTALL_LOCK = threading.Lock()
_INSTALLED = False

RU_SERVICE_WORDS = frozenset(
    {
        "а", "без", "бы", "в", "во", "да", "для", "до", "же", "за", "и",
        "из", "или", "к", "ко", "ли", "между", "на", "над", "не", "ни", "но",
        "о", "об", "от", "по", "под", "при", "про", "с", "со", "у", "через",
    }
)

_PRESERVE_CASE = {
    "esv": "ESV",
    "kjv": "KJV",
    "nasb": "NASB",
    "niv": "NIV",
    "lsb": "LSB",
    "nlt": "NLT",
    "csb": "CSB",
    "nkjv": "NKJV",
    "rsv": "RSV",
    "net": "NET",
    "nrsv": "NRSV",
    "leb": "LEB",
    "asv": "ASV",
    "lbcf": "LBCF",
    "lbcf1689": "LBCF1689",
    "wcf": "WCF",
    "tulip": "TULIP",
    "q&a": "Q&A",
    "qa": "QA",
    "youtube": "YouTube",
    "rutube": "RuTube",
    "vk": "VK",
    "iphone": "iPhone",
    "ipad": "iPad",
    "na28": "NA28",
    "bhs": "BHS",
    "lxx": "LXX",
}

_EDGE_RE = re.compile(r"^([^А-Яа-яЁёA-Za-z0-9]*)(.*?)([^А-Яа-яЁёA-Za-z0-9]*)$")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_DELIVERY_MARKERS = tuple(
    sorted(
        {
            " — русский дубляж",
            " — только русский голос",
            " — русские субтитры",
            " — исходные субтитры",
            " — точная расшифровка",
            " — шаблон перевода",
            " — проверка перевода",
            " — перевод",
        },
        key=len,
        reverse=True,
    )
)


def _split_edges(token: str) -> tuple[str, str, str]:
    match = _EDGE_RE.match(token)
    return match.groups() if match else ("", token, "")


def _capitalize(word: str) -> str:
    for index, char in enumerate(word):
        if char.isalpha():
            return word[:index] + char.upper() + word[index + 1 :]
    return word


def canonical_media_title(value: Any) -> str:
    """Return the canonical Russian media-title casing.

    Service words are tested before acronym/proper-case preservation. This is
    crucial for one-letter uppercase tokens: ``И`` is a conjunction, not an
    acronym, unless it is the first word of the title.
    """
    text = re.sub(r"\s+", " ", str(value or "")).strip(" .—–-")
    text = re.sub(r"\s+[—–-]\s+", " - ", text)
    if not text or not _CYRILLIC_RE.search(text):
        return text

    result: list[str] = []
    for index, raw in enumerate(text.split()):
        prefix, core, suffix = _split_edges(raw)
        if not core:
            result.append(raw)
            continue

        folded = core.casefold()
        if index > 0 and folded in RU_SERVICE_WORDS:
            normalized = core.lower()
        elif folded in _PRESERVE_CASE:
            normalized = _PRESERVE_CASE[folded]
        elif re.search(r"[а-яё][А-ЯЁ]", core) or re.search(r"-[А-ЯЁ]", core):
            normalized = core
        elif len(core) >= 2 and core.isupper() and core.isalpha():
            normalized = core
        else:
            normalized = _capitalize(core.lower())
        result.append(prefix + normalized + suffix)

    return normalize_person_names(" ".join(result))


def canonical_delivery_filename(value: Any) -> str:
    """Fix only the title portion of a user-facing filename."""
    filename = Path(str(value or "")).name
    if not filename:
        return filename
    suffix = Path(filename).suffix
    stem = filename[: -len(suffix)] if suffix else filename
    if not _CYRILLIC_RE.search(stem):
        return filename

    folded = stem.casefold()
    for marker in _DELIVERY_MARKERS:
        position = folded.find(marker)
        if position > 0:
            title = canonical_media_title(stem[:position])
            return title + stem[position:] + suffix
    return canonical_media_title(stem) + suffix


def install_voxcpm_title_policy(runtime_module: ModuleType) -> None:
    """Wrap the existing VoxCPM title standard without losing author handling."""
    original = getattr(runtime_module, "standardize_russian_title", None)
    if not callable(original) or getattr(original, "_canonical_media_title", False):
        return

    def wrapped(value: str, *, context: str = "") -> str:
        return canonical_media_title(original(value, context=context))

    wrapped._canonical_media_title = True  # type: ignore[attr-defined]
    runtime_module.standardize_russian_title = wrapped


def _patch_core_title_case() -> None:
    """Replace old imported Shorts/Clips formatters with the canonical function."""
    try:
        import core.text_utils as text_utils
    except Exception:
        return

    old = getattr(text_utils, "title_case_fragment", None)
    if old is canonical_media_title:
        return
    text_utils.title_case_fragment = canonical_media_title
    for module in list(sys.modules.values()):
        if module is None:
            continue
        try:
            if getattr(module, "title_case_fragment", None) is old:
                setattr(module, "title_case_fragment", canonical_media_title)
        except Exception:
            continue


def _patch_dub_store() -> None:
    from services.dub_studio import DubStore

    original = DubStore._row_project
    if getattr(original, "_canonical_media_title", False):
        return

    def wrapped(self: Any, row: Any) -> dict[str, Any] | None:
        project = original(self, row)
        if project and project.get("title"):
            project["title"] = canonical_media_title(project["title"])
        return project

    wrapped._canonical_media_title = True  # type: ignore[attr-defined]
    DubStore._row_project = wrapped


def _patch_notifications() -> None:
    try:
        import services.dub_studio_runtime as runtime
    except Exception:
        return

    original = runtime._undelivered_notification_events
    if getattr(original, "_canonical_media_title", False):
        return

    def wrapped(store: Any, limit: int = 20) -> list[dict[str, Any]]:
        events = original(store, limit=limit)
        for event in events:
            if event.get("project_title"):
                event["project_title"] = canonical_media_title(event["project_title"])
        return events

    wrapped._canonical_media_title = True  # type: ignore[attr-defined]
    runtime._undelivered_notification_events = wrapped


def _patch_delivery() -> None:
    try:
        import handlers.dub_delivery as delivery
    except Exception:
        return

    original = delivery.available_outputs
    if getattr(original, "_canonical_media_title", False):
        return

    def wrapped(project: dict[str, Any], *, include_all_video: bool = False) -> list[dict[str, Any]]:
        rows = original(project, include_all_video=include_all_video)
        for row in rows:
            row["filename"] = canonical_delivery_filename(row.get("filename") or "")
        return rows

    wrapped._canonical_media_title = True  # type: ignore[attr-defined]
    delivery.available_outputs = wrapped
    try:
        import handlers.dub_commands as commands

        commands.available_outputs = wrapped
    except Exception:
        pass


def _patch_health() -> None:
    try:
        import handlers.dub_health as health
    except Exception:
        return

    original = health.collect_dub_health
    if getattr(original, "_canonical_media_title", False):
        return

    def wrapped() -> list[dict[str, Any]]:
        checks = original()
        repo = Path(__file__).resolve().parent.parent
        route_names = (
            "generic_clean_gemini_runtime.py",
            "generic_clean_direct_runtime.py",
            "generic_clean_custom_runtime.py",
        )
        try:
            route_sources = [
                (repo / "tools" / "voxcpm2" / name).read_text(encoding="utf-8")
                for name in route_names
            ]
            own_source = Path(__file__).read_text(encoding="utf-8")
        except OSError:
            route_sources = []
            own_source = ""
        title_ok = bool(
            canonical_media_title(
                "Сила И Достоинство Благочестивой Женщины - Джон Пайпер"
            )
            == "Сила и Достоинство Благочестивой Женщины - Джон Пайпер"
            and canonical_delivery_filename(
                "Сила И Достоинство - Джон Пайпер — русский дубляж.mp4"
            )
            == "Сила и Достоинство - Джон Пайпер — русский дубляж.mp4"
            and len(route_sources) == len(route_names)
            and all("install_voxcpm_title_policy" in source for source in route_sources)
            and all("force_fresh=True" in source for source in route_sources)
            and "runtime._undelivered_notification_events = wrapped" in own_source
            and "text_utils.title_case_fragment = canonical_media_title" in own_source
        )
        for item in checks:
            if item.get("label") == "Clean Expressive NoChew + независимый QA":
                item["ok"] = bool(item.get("ok")) and title_ok
                item["detail"] = (
                    str(item.get("detail") or "")
                    + "; единый русский Title Case; fresh full baselines"
                )
                break
        return checks

    wrapped._canonical_media_title = True  # type: ignore[attr-defined]
    health.collect_dub_health = wrapped


def _patch_livedub() -> None:
    try:
        import services.livedub_output_policy as output_policy

        output_policy._russian_heading_case = canonical_media_title
    except Exception:
        pass


def install_dub_title_policy() -> None:
    """Install one title function across all loaded bot surfaces."""
    global _INSTALLED
    if _INSTALLED:
        return
    with _INSTALL_LOCK:
        if _INSTALLED:
            return
        _patch_core_title_case()
        _patch_dub_store()
        _patch_notifications()
        _patch_delivery()
        _patch_health()
        _patch_livedub()
        _INSTALLED = True


__all__ = [
    "RU_SERVICE_WORDS",
    "canonical_delivery_filename",
    "canonical_media_title",
    "install_dub_title_policy",
    "install_voxcpm_title_policy",
]
