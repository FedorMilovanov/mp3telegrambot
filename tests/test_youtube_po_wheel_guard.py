from importlib import metadata

import pytest

from services import youtube_po_token_runtime as po


def test_reintroduced_bgutil_wheel_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def installed_version(name: str) -> str:
        if name == po.BGUTIL_DISTRIBUTION:
            return "1.3.1"
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(po.metadata, "version", installed_version)

    with pytest.raises(po.YouTubePoTokenRuntimeError) as caught:
        po._require_no_installed_bgutil_wheel()

    message = str(caught.value)
    assert "source tree" in message
    assert "pip uninstall -y bgutil-ytdlp-pot-provider" in message


def test_missing_bgutil_wheel_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(po.metadata, "version", missing)
    po._require_no_installed_bgutil_wheel()
