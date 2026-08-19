from pathlib import Path


def test_direct_entrypoint_self_heals_before_fail_closed_runtime_validation() -> None:
    source = Path("bot_new.py").read_text(encoding="utf-8")

    provision = source.index("ensure_bgutil_provider()")
    validate = source.index("require_youtube_po_token_runtime()")
    runtime_bootstrap = source.index("bootstrap_pre_main()")

    assert provision < validate < runtime_bootstrap
    assert "git pull" in source
    assert "format 18/360p fallback не используется" in source
