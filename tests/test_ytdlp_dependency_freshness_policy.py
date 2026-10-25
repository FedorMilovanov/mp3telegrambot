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


def test_dependabot_tracks_the_youtube_pair_daily_and_keeps_other_pins_moving() -> None:
    config_path = ROOT / ".github" / "dependabot.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert config.get("version") == 2
    updates = config.get("updates")
    assert isinstance(updates, list)

    # Dependabot rejects two configs with the same (ecosystem, directory,
    # target-branch): "overlapping directories" is a hard error.
    keys = [
        (item.get("package-ecosystem"), item.get("directory"), item.get("target-branch"))
        for item in updates
        if isinstance(item, dict)
    ]
    assert len(keys) == len(set(keys)), f"duplicate dependabot entry: {keys}"

    pip_updates = [
        item
        for item in updates
        if isinstance(item, dict) and item.get("package-ecosystem") == "pip" and item.get("directory") == "/"
    ]
    assert len(pip_updates) == 1
    policy = pip_updates[0]

    # 1. YouTube extractor breakage is a production incident: daily tracking.
    assert policy["schedule"]["interval"] == "daily"

    groups = policy["groups"]
    assert list(groups)[0] == "youtube-downloader", "the pair must match first"
    youtube_group = groups["youtube-downloader"]
    assert set(youtube_group.get("patterns") or []) == {"yt-dlp", "yt-dlp-ejs"}

    # 2. Everything else must still be proposed — the old config let 40+ pins
    #    rot, and a group may only claim a package once.
    assert set(groups) == {"youtube-downloader", "pip-minor-and-patch", "pip-major"}
    for name in ("pip-minor-and-patch", "pip-major"):
        assert set(groups[name].get("exclude-patterns") or []) == {"yt-dlp", "yt-dlp-ejs"}
    assert set(groups["pip-minor-and-patch"]["update-types"]) == {"minor", "patch"}
    assert set(groups["pip-major"]["update-types"]) == {"major"}

    assert policy["open-pull-requests-limit"] > 1
