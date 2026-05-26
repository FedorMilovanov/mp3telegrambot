"""Tests for v3 patch 16 — editPage rate limit fix + audio key_categories hint.

Root cause of editPage failures: Telegraph API needs ~2s between createPage and editPage.
Without pause: 0.7s gap → rate limit → 3/3 failures even with exponential backoff.
With sleep(2): stable across all node counts including 87 nodes.

Changes:
- services/telegraph.py: sleep(2) before editPage loop in Synopsis
- services/telegraph_pages.py: sleep(2) before first editPage in expanded pipeline
- core/prompts.py: audio key_categories hint for theological precision
"""


def test_telegraph_synopsis_has_sleep_before_editpage():
    src = open("services/telegraph.py", encoding="utf-8").read()
    # Sleep must be between "опубликовано" and the editPage for loop
    pub_pos = src.find("опубликовано")
    edit_pos = src.find("for i, (part_secs, page_url) in enumerate(published_parts):", pub_pos)
    # Between these two positions, sleep(2) must appear
    between = src[pub_pos:edit_pos]
    assert "asyncio.sleep(2)" in between, \
        "telegraph.py must have sleep(2) between publish and editPage loop"


def test_telegraph_synopsis_sleep_has_comment():
    src = open("services/telegraph.py", encoding="utf-8").read()
    assert "V3-P16" in src, \
        "telegraph.py must have V3-P16 comment explaining the sleep"


def test_telegraph_pages_has_sleep_before_editpage():
    src = open("services/telegraph_pages.py", encoding="utf-8").read()
    # Find sleep before editPage in _publish_expanded_page
    assert "if i == 0:  # пауза только перед первым editPage" in src or \
           "V3-P16: пауза перед editPage" in src, \
        "telegraph_pages.py must have sleep before editPage"


def test_audio_prompt_keycategories_has_theological_hint():
    from core.prompts import build_audio_analysis_prompt
    p = build_audio_analysis_prompt("Test", "Chan", "50:00", 3000)
    assert "монергизм" in p or "богословски точные термины" in p, \
        "Audio prompt key_categories must hint at theological precision"


def test_audio_prompt_keycategories_explains_source_pack():
    from core.prompts import build_audio_analysis_prompt
    p = build_audio_analysis_prompt("Test", "Chan", "50:00", 3000)
    assert "тематический пакет источников" in p, \
        "Audio prompt must explain that key_categories build source pack"


def test_all_prompts_still_valid():
    """Ensure all main prompts compile and have no unresolved shared placeholders."""
    import re
    from core.prompts import (SYNOPSIS_PROMPT_V2, SYNOPSIS_PROMPT_QA,
        STUDY_ANALYSIS_PROMPT, REFLECTION_APPLICATION_PROMPT)
    shared = re.compile(
        r"\{(INLINE_TIMESTAMP_RULES|STRICT_BANS|STRICT_BANS_STUDY|QA_TIMESTAMP_RULE"
        r"|THIRD_PERSON_BAN|FEW_SHOT_FIRST_SECTION|VERIFIED_CONTEXT_RULE)\}"
    )
    for name, p in [("V2", SYNOPSIS_PROMPT_V2), ("QA", SYNOPSIS_PROMPT_QA),
                    ("STUDY", STUDY_ANALYSIS_PROMPT), ("REFL", REFLECTION_APPLICATION_PROMPT)]:
        found = shared.findall(p)
        assert not found, f"{name} has unresolved: {found}"

