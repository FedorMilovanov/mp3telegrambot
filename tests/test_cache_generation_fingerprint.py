"""Cache invalidation must track Telegraph rendering contract, not only prompts."""
from pathlib import Path


def test_prompt_fingerprint_hashes_renderer_contract_files():
    src = Path("core/database.py").read_text(encoding="utf-8")
    for rel in (
        "core/prompts.py",
        "converters/md_telegraph.py",
        "services/telegraph.py",
        "services/telegraph_pages.py",
        "services/youtube_transcript.py",
        "core/source_titles.py",
    ):
        assert rel in src
    assert "видимых" in src and "timestamp" in src


def test_get_prompt_fingerprint_returns_stable_short_hash():
    from core.database import get_prompt_fingerprint
    fp = get_prompt_fingerprint()
    assert isinstance(fp, str)
    assert len(fp) == 16
    int(fp, 16)
