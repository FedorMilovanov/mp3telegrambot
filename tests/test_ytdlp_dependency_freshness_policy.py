from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"\d+(?:\.\d+)+", value.strip())
    assert match, f"expected numeric dotted version, got {value!r}"
    return tuple(int(part) for part in value.split("."))


def _locked_version(distribution: str) -> str:
    text = (ROOT / "requirements-lock.txt").read_text(encoding="utf-8")
    match = re.search(
        rf"(?m)^{re.escape(distribution)}==(?P<version>\d+(?:\.\d+)+)$",
        text,
    )
    assert match, f"{distribution} must stay exactly pinned in requirements-lock.txt"
    return match.group("version")


def test_locked_ytdlp_satisfies_declared_production_floor() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    match = re.search(
        r"(?m)^yt-dlp\[default\]>=([0-9]+(?:\.[0-9]+)+)$",
        requirements,
    )
    assert match, "requirements.txt must keep an explicit yt-dlp production floor"

    assert _version_tuple(_locked_version("yt-dlp")) >= _version_tuple(match.group(1))
    _locked_version("yt-dlp-ejs")


def test_dependabot_tracks_the_youtube_pair_daily_and_everything_else_weekly() -> None:
    config_path = ROOT / ".github" / "dependabot.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert config.get("version") == 2
    updates = config.get("updates")
    assert isinstance(updates, list)

    pip_updates = [
        item
        for item in updates
        if isinstance(item, dict)
        and item.get("package-ecosystem") == "pip"
        and item.get("directory") == "/"
    ]
    assert len(pip_updates) == 2

    youtube = next(item for item in pip_updates if item.get("allow"))
    general = next(item for item in pip_updates if not item.get("allow"))

    # 1. YouTube extractor breakage is a production incident: daily tracking.
    assert youtube["schedule"]["interval"] == "daily"
    assert {
        entry.get("dependency-name") for entry in youtube["allow"]
    } == {"yt-dlp", "yt-dlp-ejs"}
    youtube_group = youtube["groups"]["youtube-downloader"]
    assert set(youtube_group.get("patterns") or []) == {"yt-dlp", "yt-dlp-ejs"}
    assert youtube["open-pull-requests-limit"] >= 1

    # 2. Everything else must still be proposed — the old config let 40+ pins rot.
    assert general["schedule"]["interval"] == "weekly"
    assert general["open-pull-requests-limit"] > 1
    assert {
        entry.get("dependency-name") for entry in general["ignore"]
    } == {"yt-dlp", "yt-dlp-ejs"}, "the pair must not get two competing PRs"
    assert set(general["groups"]) == {"pip-minor-and-patch", "pip-major"}
