"""Do not restore unused historical glossary callbacks to the MP3Bot runtime."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_1525_glossary_handler_is_retired_without_disrupting_normal_callbacks() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "lordchrist_1525_glossary" not in source
    assert "register_lordchrist_1525_glossary" not in source
    assert not (ROOT / "handlers" / "lordchrist_1525_glossary.py").exists()
    assert "app.add_handler(CallbackQueryHandler(handle_mode_callback" in source
    assert "app.add_handler(CallbackQueryHandler(handle_callback))" in source
