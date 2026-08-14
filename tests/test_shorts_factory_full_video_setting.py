import sqlite3

import handlers.mode_command as mode_command


def test_factory_full_video_setting_persists_per_user(monkeypatch, tmp_path):
    db_path = tmp_path / "settings.sqlite3"

    def connect():
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        return conn

    monkeypatch.setattr(mode_command, "_db_conn", connect)

    assert mode_command._get_shorts_factory_full_video_raw(101) is False
    assert mode_command._get_shorts_factory_full_video_raw(202) is False

    mode_command._set_shorts_factory_full_video_raw(101, True)
    assert mode_command._get_shorts_factory_full_video_raw(101) is True
    assert mode_command._get_shorts_factory_full_video_raw(202) is False

    mode_command._set_shorts_factory_full_video_raw(101, False)
    assert mode_command._get_shorts_factory_full_video_raw(101) is False
