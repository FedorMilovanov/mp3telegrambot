from pathlib import Path

from services import youtube_po_token_runtime as runtime
from tools import ensure_bgutil_provider as provisioner


def test_direct_entrypoint_self_heals_before_fail_closed_runtime_validation() -> None:
    source = Path("bot_new.py").read_text(encoding="utf-8")

    provision = source.index("ensure_bgutil_provider()")
    validate = source.index("require_youtube_po_token_runtime()")
    runtime_bootstrap = source.index("bootstrap_pre_main()")

    assert provision < validate < runtime_bootstrap
    assert "git pull" in source
    assert "format 18/360p fallback не используется" in source
    assert "_provider_home_override" not in source
    assert "BGUTIL_PROVIDER_HOME конфликтует" not in source


def test_provisioner_readiness_and_ytdlp_policy_share_one_provider_identity() -> None:
    config = Path("yt-dlp.conf").read_text(encoding="utf-8")

    assert runtime.DEFAULT_PROVIDER_ROOT.resolve() == provisioner.PROVIDER_ROOT.resolve()
    assert runtime.DEFAULT_PROVIDER_HOME.resolve() == provisioner.SERVER_ROOT.resolve()
    assert (
        "youtubepot-bgutilscript:server_home=.runtime/bgutil-ytdlp-pot-provider/server"
        in config
    )


def test_provisioner_and_readiness_share_exact_provider_identity() -> None:
    assert runtime.BGUTIL_EXPECTED_VERSION == provisioner.BGUTIL_VERSION
    assert runtime.BGUTIL_EXPECTED_COMMIT == provisioner.BGUTIL_COMMIT


def test_provisioner_enforces_upstream_node_and_npm_floors() -> None:
    source = Path("tools/ensure_bgutil_provider.py").read_text(encoding="utf-8")

    assert "if major < 22" in source
    assert "if major < 9" in source
    assert "_npm_executable()" in source
