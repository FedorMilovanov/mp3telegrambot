#!/usr/bin/env python3
"""One-shot deterministic codemod for source-owned Conspect policy."""
from pathlib import Path
import re


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one anchor, got {count}: {old[:100]!r}")
    write(path, text.replace(old, new, 1))


# ---------------------------------------------------------------------------
# Runtime manifest: Conspect is source-owned, so no bootstrap mutator remains.
# ---------------------------------------------------------------------------
path = "services/runtime_manifest.py"
text = read(path)
pattern = r'''\n    RuntimeFeature\(\n        "conspect-quality-bootstrap",\n        "services\.conspect_bootstrap",\n        "configure_conspect_runtime",\n        RuntimePhase\.PRE_MAIN,\n    \),'''
text, count = re.subn(pattern, "", text, count=1)
if count != 1:
    raise SystemExit(f"manifest conspect bootstrap removal count={count}")
write(path, text)

# ---------------------------------------------------------------------------
# Candidate schema directly declares the word-study contract.
# ---------------------------------------------------------------------------
path = "core/candidate_schema.py"
text = read(path)
old = '''                    "thesis", "argument_spine", "pull_quote", "application",
                ],'''
new = '''                    "thesis", "argument_spine", "pull_quote", "application", "word_study",
                ],'''
if text.count(old) != 1:
    raise SystemExit("candidate_schema enum anchor mismatch")
text = text.replace(old, new, 1)
old = '''            "anchor_timestamp": _string_schema(),
            "concrete_step": _string_schema(),'''
new = '''            "anchor_timestamp": _string_schema(),
            "concrete_step": _string_schema(),
            "scripture_ref": _string_schema(),
            "russian_quote": _string_schema(),
            "russian_focus": _string_schema(),
            "original_form": _string_schema(),
            "transliteration": _string_schema(),
            "russian_pronunciation": _string_schema(),
            "grammar": _string_schema(),
            "basic_meaning": _string_schema(),
            "meaning_in_context": _string_schema(),
            "limits_of_claim": _string_schema(),
            "source": _string_schema(),'''
if text.count(old) != 1:
    raise SystemExit("candidate_schema word-study fields anchor mismatch")
text = text.replace(old, new, 1)
write(path, text)

# ---------------------------------------------------------------------------
# Structured blocks own word-study normalization directly.
# ---------------------------------------------------------------------------
path = "core/structured_blocks.py"
text = read(path)
old = '''    block = dict(raw)
    original_type = str(block.get("type") or "paragraph").strip().lower()
    btype = canonical_block_type(original_type)'''
new = '''    block = dict(raw)
    original_type = str(block.get("type") or "paragraph").strip().lower()
    if original_type in {"word_study", "wordstudy"}:
        from core.study_quality import normalize_word_study_or_drop

        return normalize_word_study_or_drop(block)
    btype = canonical_block_type(original_type)'''
if text.count(old) != 1:
    raise SystemExit("structured_blocks word-study owner anchor mismatch")
text = text.replace(old, new, 1)
write(path, text)

# ---------------------------------------------------------------------------
# Narrow live typo repairs belong in the normalizer table itself.
# ---------------------------------------------------------------------------
path = "core/text_utils.py"
text = read(path)
anchor = '''    # Live-run polish: frequent Gemini/ASR Russian typos seen in Telegraph pages.
    # Kept deliberately narrow: these are unambiguous spelling/case fixes.
'''
addition = anchor + '''    ("Слово Божьего — нструмент", "Слово Божье — инструмент"),
    ("слово Божьего — нструмент", "слово Божье — инструмент"),
    ("проповедь Слово Божьего", "проповедь Слова Божьего"),
'''
if text.count(anchor) != 1:
    raise SystemExit("text_utils typo table anchor mismatch")
text = text.replace(anchor, addition, 1)
write(path, text)

# ---------------------------------------------------------------------------
# Content audit owns drop/reconcile/teacherly warnings directly.
# ---------------------------------------------------------------------------
path = "core/content_audit.py"
text = read(path)
old = '''from core.structured_blocks import normalize_structured_block
from core.synopsis_timestamps import reconcile_synopsis_timestamps'''
new = '''from core.structured_blocks import normalize_structured_block
from core.core_utils import time_to_seconds
from core.study_quality import collect_teacherly_study_warnings
from core.synopsis_timestamps import reconcile_synopsis_timestamps'''
if text.count(old) != 1:
    raise SystemExit("content_audit import anchor mismatch")
text = text.replace(old, new, 1)

anchor = '''_BULLET_LINE_RE = re.compile(r"^\\s*[•\\-]\\s+(.+?)\\s*$")
'''
addition = anchor + '''_INLINE_EXPANDED_TS_RE = re.compile(
    r"⏱️?\\s*\\*{0,2}(\\d{1,2}:\\d{2}(?::\\d{2})?)\\*{0,2}"
)


def _all_inline_expanded_timestamps(section: dict[str, Any]) -> list[str]:
    values = [str(section.get("content") or "")]
    for block in section.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        values.extend(value for value in block.values() if isinstance(value, str))
    return [
        match.group(1)
        for value in values
        for match in _INLINE_EXPANDED_TS_RE.finditer(value)
    ]
'''
if text.count(anchor) != 1:
    raise SystemExit("content_audit timestamp helper anchor mismatch")
text = text.replace(anchor, addition, 1)

old = '''                block = normalize_structured_block(raw_block) or dict(raw_block)
                block_loc = f"{base_loc}.blocks[{bidx}]"
                issues.extend(_validate_block_required_fields(block, location=block_loc))'''
new = '''                block = normalize_structured_block(raw_block) or dict(raw_block)
                block_loc = f"{base_loc}.blocks[{bidx}]"
                if block.get("_drop_word_study"):
                    issues.append(ContentAuditIssue(
                        code="word_study_dropped",
                        location=block_loc,
                        message="incomplete decorative word-study removed; page preserved",
                    ))
                    continue
                issues.extend(_validate_block_required_fields(block, location=block_loc))'''
if text.count(old) != 1:
    raise SystemExit("content_audit word-study drop anchor mismatch")
text = text.replace(old, new, 1)

anchor = '''    # Synopsis has two independently generated views of one timeline: section
    # starts and inline anchors.  Reconcile them at the audit boundary so both'''
insert = '''    # Reconcile tiny section-start rounding drift with inline evidence. Genuine
    # retrospective links (>30s) are preserved exactly.
    for sidx, section in enumerate(new_sections):
        section_time = str(section.get("time") or "").strip()
        section_sec = time_to_seconds(section_time)
        inline = [
            (stamp, time_to_seconds(stamp))
            for stamp in _all_inline_expanded_timestamps(section)
        ]
        inline = [(stamp, sec) for stamp, sec in inline if sec is not None]
        if section_sec is None or not inline:
            continue
        earliest_stamp, earliest_sec = min(inline, key=lambda item: item[1])
        delta = section_sec - earliest_sec
        if 0 < delta <= 30:
            old_time = section_time
            section["time"] = earliest_stamp
            if sidx < len(new_outline) and isinstance(new_outline[sidx], dict):
                new_outline[sidx]["time"] = earliest_stamp
            issues.append(ContentAuditIssue(
                code="section_time_reconciled",
                location=f"{label or 'expanded'}.sections[{sidx}].time",
                message=f"section start moved {delta}s earlier to match inline timestamp",
                before=old_time,
                after=earliest_stamp,
            ))

    if label == "StudyAnalysis":
        for finding in collect_teacherly_study_warnings(new_sections):
            issues.append(ContentAuditIssue(
                code=finding["code"],
                location=finding["location"],
                message=finding["message"],
                before=finding.get("before", ""),
            ))

''' + anchor
if text.count(anchor) != 1:
    raise SystemExit("content_audit source-owned polish insertion anchor mismatch")
text = text.replace(anchor, insert, 1)

old = '''    "section_start_non_monotonic",
}'''
new = '''    "section_start_non_monotonic",
    "study_checklist_prose_warning",
    "study_fragmented_cards_warning",
    "study_bold_anchor_missing_warning",
    "study_template_architecture_warning",
}'''
if text.count(old) != 1:
    raise SystemExit("content_audit warning codes anchor mismatch")
text = text.replace(old, new, 1)
write(path, text)

# ---------------------------------------------------------------------------
# Extract the actual teacherly prompt into a pure policy module.  The historical
# runtime module becomes a compatibility re-export/validator with no hooks.
# ---------------------------------------------------------------------------
old_runtime = read("services/study_synthesis_runtime.py")
start = old_runtime.index("TEACHERLY_STUDY_PROMPT =")
end = old_runtime.index("\n\n_FIELD_LABEL_RE", start)
prompt_block = old_runtime[start:end].rstrip()
policy = '''"""Source-owned effective Study Analysis generation policy."""
from __future__ import annotations

from core.study_quality import render_word_study_as_prose

''' + prompt_block + '''


def validate_teacherly_study_policy() -> str:
    if "TEACHERLY STUDY SYNTHESIS 2026-07-23" not in TEACHERLY_STUDY_PROMPT:
        raise RuntimeError("teacherly Study prompt marker is missing")
    return "teacherly Study prompt + source-owned word-study prose"


__all__ = [
    "TEACHERLY_STUDY_PROMPT",
    "render_word_study_as_prose",
    "validate_teacherly_study_policy",
]
'''
write("services/study_synthesis_policy.py", policy)
write(
    "services/study_synthesis_runtime.py",
    '''"""Compatibility facade for the former Study runtime installer.

Study policy is source-owned by ``study_synthesis_policy``, ``structured_blocks``,
``content_audit`` and ``telegraph_pages``.  This module performs no mutation and
contains no import hook.
"""
from __future__ import annotations

from core.study_quality import render_word_study_as_prose
from services.study_synthesis_policy import (
    TEACHERLY_STUDY_PROMPT,
    validate_teacherly_study_policy,
)


def install_teacherly_study_runtime() -> str:
    return validate_teacherly_study_policy()


__all__ = [
    "TEACHERLY_STUDY_PROMPT",
    "install_teacherly_study_runtime",
    "render_word_study_as_prose",
]
''',
)

# ---------------------------------------------------------------------------
# Telegraph owns its effective prompt and retry acceptance directly.
# ---------------------------------------------------------------------------
path = "services/telegraph_pages.py"
text = read(path)
old = 'from core.prompts import STUDY_ANALYSIS_PROMPT, REFLECTION_APPLICATION_PROMPT'
new = '''from core.prompts import REFLECTION_APPLICATION_PROMPT
from services.study_synthesis_policy import TEACHERLY_STUDY_PROMPT as STUDY_ANALYSIS_PROMPT'''
if text.count(old) != 1:
    raise SystemExit("telegraph_pages prompt import anchor mismatch")
text = text.replace(old, new, 1)
old = '''    if _audit_warning_count(retry_issues) <= _audit_warning_count(issues):
        logger.info(
            "%s: content audit retry accepted warnings %d -> %d",
            label, _audit_warning_count(issues), _audit_warning_count(retry_issues),
        )
        return retry_sections, retry_outline, retry_issues'''
new = '''    before_warnings = _audit_warning_count(issues)
    after_warnings = _audit_warning_count(retry_issues)
    improved = after_warnings < before_warnings
    non_study_no_worse = label != "StudyAnalysis" and after_warnings == before_warnings
    if improved or non_study_no_worse:
        logger.info(
            "%s: content audit retry accepted warnings %d -> %d",
            label, before_warnings, after_warnings,
        )
        return retry_sections, retry_outline, retry_issues'''
if text.count(old) != 1:
    raise SystemExit("telegraph_pages retry acceptance anchor mismatch")
text = text.replace(old, new, 1)
write(path, text)

# ---------------------------------------------------------------------------
# Historical Conspect installer modules remain import-compatible validators only.
# ---------------------------------------------------------------------------
path = "services/conspect_quality_contract.py"
text = read(path)
text = text.replace("from copy import deepcopy\n", "")
text = text.replace("from typing import Any, Callable", "from typing import Any")
text = text.replace("_INSTALLED = False\n", "")
pattern = r'\ndef _patch_structured_block_normalizer\(\) -> None:.*\Z'
replacement = '''

def install_conspect_quality_contract() -> str:
    """Compatibility validator; schema/normalization are source-owned."""
    from core.candidate_schema import expanded_page_response_schema

    block = (
        expanded_page_response_schema()["properties"]["sections"]["items"]
        ["properties"]["blocks"]["items"]
    )
    if "word_study" not in block["properties"]["type"].get("enum", []):
        raise RuntimeError("word_study is missing from the canonical expanded-page schema")
    return "source-owned Study schema/normalization; no runtime patching"
'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"conspect_quality_contract hook removal count={count}")
write(path, text)

write(
    "services/conspect_audit_runtime.py",
    '''"""Compatibility validator for source-owned Conspect audit rules."""
from __future__ import annotations

from typing import Any


def _normalize_legacy_lexicon(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep the historical helper importable without mutating global functions."""
    from core.structured_blocks import normalize_structured_block

    block = dict(raw or {})
    block["type"] = "lexicon"
    return normalize_structured_block(block) or block


def install_conspect_audit_runtime() -> str:
    from core.content_audit import audit_expanded_sections
    from core.structured_blocks import normalize_structured_block
    from core.text_utils import normalize_common_typos

    if "инструмент" not in normalize_common_typos("Слово Божьего — нструмент"):
        raise RuntimeError("source-owned Conspect typo repair is missing")
    dropped = normalize_structured_block({"type": "word_study"}) or {}
    if not dropped.get("_drop_word_study"):
        raise RuntimeError("source-owned incomplete word-study drop contract is missing")
    if not callable(audit_expanded_sections):
        raise RuntimeError("source-owned content audit is unavailable")
    return "source-owned Conspect typo/block/audit rules; no runtime patching"


__all__ = ["_normalize_legacy_lexicon", "install_conspect_audit_runtime"]
''',
)

# The explicit bootstrap is no longer needed because there is nothing to mutate.
Path("services/conspect_bootstrap.py").unlink()

# ---------------------------------------------------------------------------
# Architecture + behavior regression coverage.
# ---------------------------------------------------------------------------
write(
    "tests/test_source_owned_conspect_architecture.py",
    '''from __future__ import annotations

from pathlib import Path

from core.candidate_schema import expanded_page_response_schema
from core.content_audit import audit_expanded_sections
from core.structured_blocks import normalize_structured_block

ROOT = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_study_runtime_is_compatibility_only_without_import_hooks():
    src = _src("services/study_synthesis_runtime.py")
    assert "MetaPathFinder" not in src
    assert "sys.meta_path" not in src
    assert "structured_blocks.normalize_structured_block =" not in src
    assert "content_audit.audit_expanded_sections =" not in src


def test_manifest_has_no_conspect_mutator_feature():
    src = _src("services/runtime_manifest.py")
    assert '"conspect-quality-bootstrap"' not in src
    assert "services.conspect_bootstrap" not in src
    assert not (ROOT / "services/conspect_bootstrap.py").exists()


def test_word_study_schema_and_normalization_are_source_owned():
    block = (
        expanded_page_response_schema()["properties"]["sections"]["items"]
        ["properties"]["blocks"]["items"]
    )
    assert "word_study" in block["properties"]["type"]["enum"]
    for field in (
        "scripture_ref", "russian_quote", "russian_focus", "original_form",
        "transliteration", "russian_pronunciation", "grammar", "basic_meaning",
        "meaning_in_context", "limits_of_claim", "source",
    ):
        assert field in block["properties"]

    dropped = normalize_structured_block({"type": "word_study"})
    assert dropped and dropped.get("_drop_word_study") is True

    rendered = normalize_structured_block({
        "type": "word_study",
        "scripture_ref": "Ин. 1:1",
        "russian_focus": "Слово",
        "original_form": "λόγος",
        "lemma": "λόγος",
        "meaning_in_context": "слово в контексте пролога",
        "role_in_argument": "уточняет связь тезиса с текстом",
    })
    assert rendered and rendered["type"] == "paragraph"
    assert "λόγος" in rendered["text"]


def test_content_audit_owns_teacherly_warnings_and_small_time_reconcile():
    sections = [
        {
            "title": title,
            "time": "1:00",
            "content": "⏱ **0:50** " + ("Длинный связный текст. " * 45),
        }
        for title in ("Ключевые понятия", "Источники", "Ключевые тексты")
    ]
    fixed, outline, issues = audit_expanded_sections(
        sections,
        [{"title": item["title"], "time": "1:00"} for item in sections],
        label="StudyAnalysis",
    )
    assert all(item["time"] == "0:50" for item in fixed)
    assert all(item["time"] == "0:50" for item in outline)
    codes = {issue.code for issue in issues}
    assert "section_time_reconciled" in codes
    assert "study_template_architecture_warning" in codes
    assert "study_bold_anchor_missing_warning" in codes


def test_telegraph_owns_teacherly_prompt_and_strict_study_retry_improvement():
    src = _src("services/telegraph_pages.py")
    assert "study_synthesis_policy import TEACHERLY_STUDY_PROMPT as STUDY_ANALYSIS_PROMPT" in src
    assert 'improved = after_warnings < before_warnings' in src
    assert 'label != "StudyAnalysis"' in src


def test_legacy_conspect_modules_have_no_assignment_patchers():
    for rel in (
        "services/conspect_quality_contract.py",
        "services/conspect_audit_runtime.py",
    ):
        src = _src(rel)
        assert ".normalize_structured_block =" not in src
        assert ".audit_expanded_sections =" not in src
        assert ".STUDY_ANALYSIS_PROMPT =" not in src
''',
)
