from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    source = target.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement, found {count}")
    target.write_text(source.replace(old, new, 1), encoding="utf-8", newline="\n")


def patch_preflight() -> None:
    replace_once(
        "tools/voxcpm2/dub_job_preflight/__init__.py",
        "import shutil\nimport threading\nfrom typing import Any, Iterator\n",
        "import shutil\nimport threading\nimport time\nfrom typing import Any, Iterator\n",
    )
    replace_once(
        "tools/voxcpm2/dub_job_preflight/__init__.py",
        "        os.replace(temporary, path)\n",
        "        for attempt in range(20):\n"
        "            try:\n"
        "                os.replace(temporary, path)\n"
        "                break\n"
        "            except PermissionError:\n"
        "                if attempt >= 19:\n"
        "                    raise\n"
        "                time.sleep(min(0.005 * (attempt + 1), 0.05))\n",
    )


def patch_ffmpeg() -> None:
    replace_once(
        "services/ffmpeg.py",
        "\ndef _firefox_cookie_source_available(spec: str = \"firefox\") -> bool:\n",
        "\ndef _browser_profile_from_spec(spec: str) -> str:\n"
        "    \"\"\"Extract BROWSER[:PROFILE][::KEYRING] without truncating Windows drives.\"\"\"\n"
        "    _browser, separator, remainder = str(spec or \"\").partition(\":\")\n"
        "    if not separator:\n"
        "        return \"\"\n"
        "    return remainder.split(\"::\", 1)[0].strip()\n"
        "\n"
        "\n"
        "def _firefox_cookie_source_available(spec: str = \"firefox\") -> bool:\n",
    )
    replace_once(
        "services/ffmpeg.py",
        "    profile = \"\"\n"
        "    if \":\" in spec:\n"
        "        # yt-dlp: BROWSER[:PROFILE][::KEYRING]\n"
        "        profile = spec.split(\":\", 1)[1].split(\":\", 1)[0].strip()\n",
        "    # yt-dlp: BROWSER[:PROFILE][::KEYRING]. Preserve the colon in\n"
        "    # absolute Windows paths such as firefox:C:\\\\Users\\\\... .\n"
        "    profile = _browser_profile_from_spec(spec)\n",
    )


def patch_mp3_chapters() -> None:
    replace_once(
        "services/mp3_chapters.py",
        "        try:\n"
        "            tags = ID3(str(mp3_path))\n"
        "        except ID3NoHeaderError:\n"
        "            tags = ID3()\n"
        "\n"
        "        # 1. Базовые теги\n",
        "        try:\n"
        "            tags = ID3(str(mp3_path))\n"
        "        except ID3NoHeaderError:\n"
        "            tags = ID3()\n"
        "\n"
        "        metadata_requested = bool(\n"
        "            str(title or \"\").strip()\n"
        "            or str(performer or \"\").strip()\n"
        "            or str(comment or \"\").strip()\n"
        "            or (thumb_path and Path(thumb_path).exists())\n"
        "        )\n"
        "        chapters_embedded = False\n"
        "\n"
        "        # 1. Базовые теги\n",
    )
    replace_once(
        "services/mp3_chapters.py",
        "                logger.info(\"MP3 metadata: вшито %d глав\", len(child_ids))\n",
        "                chapters_embedded = True\n"
        "                logger.info(\"MP3 metadata: вшито %d глав\", len(child_ids))\n",
    )
    replace_once(
        "services/mp3_chapters.py",
        "        tags.save(str(mp3_path))\n"
        "        return True\n",
        "        if not chapters_embedded and not metadata_requested:\n"
        "            return False\n"
        "        tags.save(str(mp3_path))\n"
        "        return True\n",
    )


def patch_tests() -> None:
    replace_once(
        "tests/test_livedub_qa.py",
        "def test_video_cpu_preset_knob(monkeypatch):\n"
        "    import services.ffmpeg as ff\n"
        "    monkeypatch.setattr(ff, \"_VIDEO_ENCODER\", None)\n"
        "    monkeypatch.setenv(\"WHISPER_FORCE_CPU\", \"1\")\n"
        "    monkeypatch.setenv(\"VIDEO_CPU_PRESET\", \"medium\")\n",
        "def test_video_cpu_preset_knob(monkeypatch):\n"
        "    import services.ffmpeg as ff\n"
        "    monkeypatch.setattr(ff, \"_VIDEO_ENCODER\", None)\n"
        "    monkeypatch.setenv(\"WHISPER_FORCE_CPU\", \"1\")\n"
        "    monkeypatch.setenv(\"VIDEO_FORCE_CPU\", \"1\")\n"
        "    monkeypatch.setenv(\"VIDEO_CPU_PRESET\", \"medium\")\n",
    )
    replace_once(
        "tests/test_livedub_qa.py",
        "    import sqlite3\n"
        "    row = sqlite3.connect(out).execute(\n"
        "        \"SELECT audio_file_id FROM video_cache WHERE video_id=?\", (\"v1\",)).fetchone()\n"
        "    assert row and row[0] == \"fid\"  # копия валидна и полна\n",
        "    import sqlite3\n"
        "    with sqlite3.connect(out) as check_conn:\n"
        "        row = check_conn.execute(\n"
        "            \"SELECT audio_file_id FROM video_cache WHERE video_id=?\",\n"
        "            (\"v1\",),\n"
        "        ).fetchone()\n"
        "    assert row and row[0] == \"fid\"  # копия валидна и полна\n",
    )


def main() -> None:
    patch_preflight()
    patch_ffmpeg()
    patch_mp3_chapters()
    patch_tests()


if __name__ == "__main__":
    main()
