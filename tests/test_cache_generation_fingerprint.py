"""Cache invalidation must track the full persisted generation/render contract."""
import os
from pathlib import Path


def test_prompt_fingerprint_hashes_generation_and_renderer_contract_files():
    src = Path("core/database.py").read_text(encoding="utf-8")
    for rel in (
        "core/prompts.py",
        "core/json_parser.py",
        "core/page_audit.py",
        "core/prompt_compactor.py",
        "core/source_packs.py",
        "core/structured_blocks.py",
        "core/synopsis_timestamps.py",
        "core/title_topic_audit.py",
        "core/telegraph_contract.py",
        "converters/md_telegraph.py",
        "services/study_synthesis_policy.py",
        "services/study_synthesis_runtime.py",
        "services/gemini_analyze.py",
        "services/telegraph.py",
        "services/telegraph_edit.py",
        "services/telegraph_pages.py",
        "services/youtube_transcript.py",
    ):
        assert rel in src
    assert "_GENERATION_CONTRACT_FILES" in src
    assert "видимых" in src and "timestamp" in src


def test_generation_contract_manifest_is_unique_and_points_to_real_files():
    from core.database import _GENERATION_CONTRACT_FILES

    assert len(_GENERATION_CONTRACT_FILES) == len(set(_GENERATION_CONTRACT_FILES))
    for rel in _GENERATION_CONTRACT_FILES:
        assert Path(rel).is_file(), rel


def test_semantic_generation_env_rotates_prompt_fingerprint(monkeypatch):
    from core.database import get_prompt_fingerprint

    before = get_prompt_fingerprint()
    current = os.getenv("SYNOPSIS_YT_TRANSCRIPT_MAX_CHARS", "120000")
    replacement = "123457" if current != "123457" else "123458"
    monkeypatch.setenv("SYNOPSIS_YT_TRANSCRIPT_MAX_CHARS", replacement)
    after = get_prompt_fingerprint()
    assert after != before


def test_operational_timeout_does_not_rotate_prompt_fingerprint(monkeypatch):
    from core.database import get_prompt_fingerprint

    before = get_prompt_fingerprint()
    monkeypatch.setenv("AUDIO_STRUCTURED_TIMEOUT", "599")
    after = get_prompt_fingerprint()
    assert after == before


def test_get_prompt_fingerprint_returns_stable_short_hash():
    from core.database import get_prompt_fingerprint

    fp = get_prompt_fingerprint()
    assert isinstance(fp, str)
    assert len(fp) == 16
    int(fp, 16)
