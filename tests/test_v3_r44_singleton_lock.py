"""Regression tests for R44 — singleton-instance guard.

Live log (2026-07-11) showed a real production incident: a second instance of
the bot was started while the first was still running. Both fought over
Telegram's getUpdates long-poll — the log showed a repeating
'Conflict: terminated by other getUpdates request' every ~7s, forever, with
NEITHER instance ever receiving a message (the first looked "alive" in logs
but was just stuck retrying). Root cause: nothing detected or refused a
second launch before hitting Telegram.

_acquire_singleton_lock() now runs first thing in run_bot_async() (before any
Telegram/network work) and writes/checks a PID lock file so a duplicate
launch fails fast with an honest message instead of colliding at the
Telegram layer minutes later.
"""

import os

import main


def test_r44_fresh_lock_acquired(tmp_path, monkeypatch):
    lock = tmp_path / "bot.lock"
    monkeypatch.setattr(main, "_SINGLETON_LOCK_PATH", lock)
    assert not lock.exists()
    assert main._acquire_singleton_lock() is True
    assert lock.exists()
    assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_r44_reacquire_same_pid_succeeds(tmp_path, monkeypatch):
    # crash-restart within the SAME process (run_bot()'s retry loop) must not
    # be blocked by the lock it itself is holding.
    lock = tmp_path / "bot.lock"
    monkeypatch.setattr(main, "_SINGLETON_LOCK_PATH", lock)
    lock.write_text(str(os.getpid()), encoding="utf-8")
    assert main._acquire_singleton_lock() is True


def test_r44_stale_dead_pid_is_overwritten(tmp_path, monkeypatch):
    lock = tmp_path / "bot.lock"
    monkeypatch.setattr(main, "_SINGLETON_LOCK_PATH", lock)
    lock.write_text("999999999", encoding="utf-8")  # implausible/dead pid
    assert main._pid_is_running(999999999) is False
    assert main._acquire_singleton_lock() is True
    assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_r44_live_other_pid_blocks_startup(tmp_path, monkeypatch):
    lock = tmp_path / "bot.lock"
    monkeypatch.setattr(main, "_SINGLETON_LOCK_PATH", lock)
    real_pid = os.getpid()
    lock.write_text(str(real_pid), encoding="utf-8")
    # pretend we're a different process than the one in the lock file — the
    # real PID (ourselves) is genuinely alive, so this must be refused.
    monkeypatch.setattr(os, "getpid", lambda: real_pid + 1)
    assert main._acquire_singleton_lock() is False
    # lock file must be untouched (still points at the "other" live instance)
    assert lock.read_text(encoding="utf-8").strip() == str(real_pid)


def test_r44_malformed_lock_file_does_not_crash(tmp_path, monkeypatch):
    lock = tmp_path / "bot.lock"
    monkeypatch.setattr(main, "_SINGLETON_LOCK_PATH", lock)
    lock.write_text("not-a-pid", encoding="utf-8")
    assert main._acquire_singleton_lock() is True


def test_r44_release_removes_only_own_lock(tmp_path, monkeypatch):
    lock = tmp_path / "bot.lock"
    monkeypatch.setattr(main, "_SINGLETON_LOCK_PATH", lock)
    lock.write_text(str(os.getpid()), encoding="utf-8")
    main._release_singleton_lock()
    assert not lock.exists()

    lock.write_text("123456789", encoding="utf-8")
    main._release_singleton_lock()
    assert lock.exists(), "must never delete a lock that isn't ours"


def test_r44_run_bot_async_checks_lock_before_network_work():
    src = open("main.py", encoding="utf-8").read()
    i_token = src.index('logger.error("❌ BOT_TOKEN не найден!")')
    i_lock = src.index("_acquire_singleton_lock()", i_token)
    i_video_locks = src.index("_video_processing_locks", i_token)
    assert i_lock < i_video_locks, "lock check must run before any other startup work"


def test_r44_run_bot_stops_without_restart_loop_on_conflict():
    src = open("main.py", encoding="utf-8").read()
    assert 'result == "singleton_conflict"' in src
    block = src[src.index('result == "singleton_conflict"'):][:500]
    assert "break" in block
    # must NOT fall through into the retry/backoff path used for real crashes
    assert "_net_fail_streak = 0" not in block.split("break")[0]
