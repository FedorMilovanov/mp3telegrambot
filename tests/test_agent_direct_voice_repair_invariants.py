"""Documentation contract for surgical human-agent dubbing repairs.

This is not an acoustic or semantic audio validator. It prevents regression of
the review gates after the 2007 Piper/MacArthur tempo and overlap incidents.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "docs" / "agent_direct_dubbing"


def guide() -> str:
    return (ROOT / "VOICE_REPAIR_AND_MODEL_PRACTICE.md").read_text(encoding="utf-8")


def test_tempo_review_distinguishes_native_speech_from_slot() -> None:
    content = guide()
    assert "### 3A. Системный аудит темпа и зависимостей патчей" in content
    for field in ("native_speech_duration", "placed_speech_duration", "slot_duration"):
        assert field in content
    assert "Пустое место в окне не означает замедление" in content
    assert "10%" in content and "15%" in content
    assert "пороги внимания" in content
    assert "всех" in content and "вставок" in content


def test_patch_overlap_cannot_undo_corrected_meaning() -> None:
    content = guide()
    for term in (
        "карту пересечений",
        "всех поколений патчей",
        "библейскую ссылку",
        "богословский тезис",
        "точное совпадение PCM вне разрешённых окон",
    ):
        assert term in content
    assert "EN/RU-сверки" in content


def test_reuse_native_audio_before_cpu_tts_with_post_aac_gates() -> None:
    content = guide()
    assert "без новой CPU-генерации" in content
    assert "не более одной обоснованной адресной TTS-попытки" in content
    assert "готового AAC" in content
    for gate in ("TECH_QA_PASS", "ASR_REVIEWED", "LISTENING_APPROVED", "EDITORIAL_APPROVED"):
        assert gate in content
    assert "не являются `LISTENING_APPROVED`" in content


def test_authored_voice_text_does_not_invent_dates_or_book_titles() -> None:
    content = guide()
    assert "названия книг и организаций, даты и годы" in content
    assert "которых нет в проверяемой английской реплике" in content


def test_preflight_precedes_repeated_full_exports() -> None:
    content = guide()
    assert "A/B-фрагмент" in content
    assert "Не плодить полноразмерные" in content
    assert "не перезаписывать" in content


def test_source_pcm_boundary_gate_is_mandatory_and_not_human_approval() -> None:
    guide_text = guide()
    agents = (ROOT.parents[1] / "AGENTS.md").read_text(encoding="utf-8")
    model_agents = (ROOT.parents[1] / "tools" / "voxcpm2" / "AGENTS.md").read_text(encoding="utf-8")
    for text in (guide_text, agents, model_agents):
        assert "direct_splice_boundary_audit" in text
        assert "HOLD_ACTIVE_SOURCE_BOUNDARY" in text
    for term in (
        "не доказывало, что границы окна находятся между полными словами",
        "старого",
        "30 мс",
        "сэмплов вне разрешённых окон",
        "READY_FOR_HUMAN_REVIEW",
        "не означает",
        "Shorts",
    ):
        assert term in guide_text
