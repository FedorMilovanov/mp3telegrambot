"""Regression tests for R43 — closing the previously-flagged decision items.

  1. QA prompt: removed the "parents/sexual immorality splice" worked example
     (confusing, not a real-world translation failure mode; risked biasing the
     model toward false `major` flags in that category). The general sentence-
     splice red-flag remains — it covers the same class of error.
  2. QA reference_block decision now accounts for the reused Gemini audio_part,
     not just original_audio_path — the model is no longer told "no audio
     attached" when audio actually was attached via reuse.
  3. Konspekt-fallback QA runs are marked low-confidence and the report shows
     an honest caveat instead of implying a full-transcript check.
  4. Subtitle translation: prompt no longer invites merging IDs across
     boundaries, and a missing ID gets one targeted retry with honest logging
     instead of a silent English fallback.
"""

from pathlib import Path

from services.livedub_qa import format_qa_report, _QA_PROMPT


# ── 1. Splice example removed ────────────────────────────────────────────────
def test_r43_qa_prompt_no_longer_contains_splice_example():
    assert "fools bring grief to their parents" not in _QA_PROMPT
    assert "fools commit sexual" not in _QA_PROMPT
    assert "родителями" not in _QA_PROMPT
    assert "инцест" not in _QA_PROMPT
    assert "прелюбодеян" not in _QA_PROMPT
    # the general (non-example-specific) splice red-flag still covers the class
    assert "склеил две соседние мысли" in _QA_PROMPT
    assert "severity=major всегда" in _QA_PROMPT


# ── 2. reference_block accounts for reused audio_part ────────────────────────
def test_r43_reference_block_considers_existing_audio_part():
    src = Path("services/livedub_qa.py").read_text(encoding="utf-8")
    assert "_will_attach_original" in src
    assert "existing_audio_part is not None and existing_client is not None" in src
    # the old bug: branch keyed ONLY on original_audio_path
    assert "if original_audio_path and Path(original_audio_path).exists():\n            reference_block" not in src


# ── 3. Low-confidence caveat surfaces in the report ──────────────────────────
def test_r43_format_qa_report_shows_low_confidence_caveat():
    out = format_qa_report({"score": 80, "verdict": "точность приемлемая", "issues": [], "_low_confidence": True})
    assert "конспекту" in out
    assert "⚠️" in out


def test_r43_format_qa_report_no_caveat_when_full_audio_used():
    out = format_qa_report({"score": 98, "verdict": "перевод точный", "issues": []})
    assert "конспекту" not in out


def test_r43_qa_marks_low_confidence_only_without_original_audio():
    src = Path("services/livedub_qa.py").read_text(encoding="utf-8")
    assert 'result.setdefault("_low_confidence", True)' in src
    assert "if not _will_attach_original:" in src


# ── 4. Subtitle ID coverage: no silent English fallback ──────────────────────
def test_r43_subtitle_prompt_forbids_cross_id_merging():
    src = Path("services/eng_subtitles.py").read_text(encoding="utf-8")
    assert "never merge two IDs into one" in src
    assert "never by dropping or merging a neighboring ID" in src


def test_r43_subtitle_translation_retries_missing_ids():
    src = Path("services/eng_subtitles.py").read_text(encoding="utf-8")
    assert "_missing = [(sid, text) for sid, text in chunk if str(sid) not in translated_segments]" in src
    assert "остались непереведёнными" in src
