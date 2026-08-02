from pathlib import Path


path = Path("tests/test_livedub_qa.py")
source = path.read_text(encoding="utf-8")
old = '''    import sqlite3
    with sqlite3.connect(out) as check_conn:
        row = check_conn.execute(
            "SELECT audio_file_id FROM video_cache WHERE video_id=?",
            ("v1",),
        ).fetchone()
    assert row and row[0] == "fid"  # копия валидна и полна
'''
new = '''    import sqlite3
    check_conn = sqlite3.connect(out)
    try:
        row = check_conn.execute(
            "SELECT audio_file_id FROM video_cache WHERE video_id=?",
            ("v1",),
        ).fetchone()
    finally:
        check_conn.close()
    assert row and row[0] == "fid"  # копия валидна и полна
'''
if source.count(old) != 1:
    raise RuntimeError(f"expected one backup connection block, found {source.count(old)}")
path.write_text(source.replace(old, new, 1), encoding="utf-8", newline="\n")
