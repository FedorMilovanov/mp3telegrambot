"""Regression tests for explicit core fallback diagnostics."""

import pathlib

import sqlite3
from typing import Any, cast

import pytest

from core import database, generated_pages, prompt_health, prompts, utils
from core import globals as core_globals


class _FailingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)
        raise sqlite3.OperationalError("database is unavailable")


def _raise_db_error() -> None:
    raise sqlite3.OperationalError("database is unavailable")


def test_db_pragma_failure_is_nonfatal() -> None:
    conn = _FailingConnection()

    database._configure_db_pragmas(cast(sqlite3.Connection, conn))

    assert conn.statements == ["PRAGMA journal_mode=WAL"]


def test_subtitle_migration_failure_is_nonfatal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(database, "_db_conn", _raise_db_error)

    database._one_time_enable_subtitles()


def test_shorts_speed_database_failure_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(database, "_db_conn", _raise_db_error)

    assert database.shorts_speed_get() == database.SHORTS_SPEED_DEFAULT


def test_setting_database_failure_uses_declared_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(database, "_db_conn", _raise_db_error)

    assert database.settings_get("shorts_subtitles") is database.SETTINGS_DEFAULTS["shorts_subtitles"]


def test_invalid_channel_mapping_keeps_existing_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHANNEL_MAPPINGS", "{")
    before = dict(database.CHANNEL_MAP)

    database.load_channel_mappings()

    assert database.CHANNEL_MAP == before


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('["a", " b "]', ["a", "b"]),
        ("a, b; c", ["a", "b", "c"]),
    ],
)
def test_json_list_supports_json_and_legacy_delimiters(raw: str, expected: list[str]) -> None:
    assert generated_pages._json_list(raw) == expected


def test_windows_path_setup_failure_keeps_inherited_path() -> None:
    class _BrokenEnvironment:
        def get(self, *_args: object) -> str:
            raise RuntimeError("environment is unavailable")

    core_globals._configure_windows_executable_path(cast(Any, _BrokenEnvironment()))


def test_audio_prompt_health_failure_skips_only_audio_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    def _broken_audio_prompt(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("prompt builder is unavailable")

    monkeypatch.setattr(prompts, "build_audio_analysis_prompt", _broken_audio_prompt)

    items = prompt_health.collect_prompt_health()

    assert items
    assert all(not item.name.startswith("AUDIO_ANALYSIS_PROMPT") for item in items)


def test_black_bar_crop_failure_returns_original_image() -> None:
    class _BrokenImage:
        def convert(self, _mode: str) -> object:
            raise RuntimeError("image conversion failed")

    image = _BrokenImage()

    assert utils._crop_black_bars_image(image) is image


def test_thumbnail_cleanup_failure_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    class _BrokenThumbsDir:
        def iterdir(self) -> object:
            raise OSError("thumbnail directory is unavailable")

    monkeypatch.setattr(utils, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(utils, "THUMBS_DIR", _BrokenThumbsDir())
    monkeypatch.setattr(utils, "_THUMBS_LAST_CLEANUP", 0.0)

    utils.cleanup_files("media", keep_mp3=False)

    assert utils._THUMBS_LAST_CLEANUP > 0


def test_extended_masking_failure_keeps_basic_api_key_mask(monkeypatch: pytest.MonkeyPatch) -> None:
    def _broken_mask(_text: str) -> str:
        raise RuntimeError("masker is unavailable")

    monkeypatch.setattr(core_globals, "mask_credentials", _broken_mask)
    api_key = "AIza" + "A" * 35

    masked = utils.mask_api_key(f"{api_key} http://user:pass@example.invalid")

    assert api_key not in masked
    assert "***APIKEY***" in masked
    assert "user:pass@" in masked
