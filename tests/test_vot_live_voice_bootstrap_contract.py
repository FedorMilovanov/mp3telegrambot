from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "vot_helper" / "vot_live.mjs"


def _source() -> str:
    return HELPER.read_text(encoding="utf-8")


def test_vot_live_helper_has_valid_node_syntax() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable")
    proc = subprocess.run(
        [node, "--check", str(HELPER)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr


def test_live_request_keeps_lively_flag_across_audio_bootstrap() -> None:
    source = _source()

    # @vot.js/node 2.4.12 drops extraOpts in its internal AUDIO_REQUESTED
    # recursion. The helper must own that bootstrap and keep every retry live.
    assert "shouldSendFailedAudio: false" in source
    assert "res.status === AUDIO_REQUESTED" in source
    assert "client.requestVtransFailAudio(videoData.url)" in source
    assert "client.requestVtransAudio(videoData.url, translationId" in source
    assert "useLivelyVoice: useLively" in source
    assert 'videoTitle: videoData.title ?? ""' in source


def test_live_success_requires_cloning_cache_confirmation() -> None:
    source = _source()

    assert "client.translateVideoCache({ videoData })" in source
    assert "cache?.cloning" in source
    assert "cloning?.status === CACHE_FINISHED" in source
    assert "Live Voices подтверждены: cloning-cache=FINISHED" in source
    assert "обычный голос не используется" in source


def test_live_output_is_not_written_before_confirmation_gate() -> None:
    source = _source()

    confirmation = source.index("cloning?.status === CACHE_FINISHED")
    output_write = source.index("fs.writeFileSync(outPath, buf)")
    assert confirmation < output_write


def test_bypass_cache_is_not_used_for_live_generation() -> None:
    # Upstream warns that bypassCache can trigger per-IP limiting. Live startup
    # must use the normal cloning cache rather than a brute-force cache bypass.
    source = _source()
    assert "bypassCache:" not in source
