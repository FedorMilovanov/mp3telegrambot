from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dub_command_and_handlers_are_registered_in_safe_order() -> None:
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'CommandHandler("dub",        dub_command' in main
    assert 'CallbackQueryHandler(handle_dub_callback, pattern="^dub:")' in main
    assert "handle_dub_translation_text" in main
    assert "handle_dub_translation_document" in main
    assert "handle_message),\n        group=1" in main
    assert main.index("handle_dub_translation_text") < main.index("handle_message),\n        group=1")


def test_runtime_dependencies_include_docx_import() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "python-docx>=1.1.0,<2.0.0" in requirements


def test_approved_translation_policy_is_explicit() -> None:
    project_code = (ROOT / "core" / "dub_projects.py").read_text(encoding="utf-8")
    assert '"translation_is_preapproved": True' in project_code
    assert '"rewrite_translation": False' in project_code
    assert '"auto_shorten_translation": False' in project_code
    assert '"shorts_max_seconds": 180.0' in project_code
    assert '"translate_on_screen_text": False' in project_code
    assert '"synthesis_engine": "VoxCPM2"' in project_code
    assert '"synthesis_device": "cpu"' in project_code
    assert '"hidden_tts_fallback": False' in project_code
