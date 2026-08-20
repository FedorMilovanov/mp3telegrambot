# yt-dlp freshness policy — 2026-08-20

## Finding

The production lock currently pins `yt-dlp==2026.7.4`. As of 2026-08-20 this is the latest stable yt-dlp release, so the installed stable line is not presently stale.

The real operational weakness was not today's version number. It was the absence of an explicit mechanism that notices the *next* stable release. `Start Bot.bat` intentionally installs the repository lock and therefore should not silently mutate the downloader at runtime.

## Decision

Keep production deterministic and reviewed:

1. `requirements-lock.txt` remains the exact runtime authority.
2. Do **not** run `pip install -U yt-dlp` during bot startup.
3. Dependabot checks the root pip requirements daily but is allow-listed to only `yt-dlp` and `yt-dlp-ejs`.
4. Those two packages are grouped into one downloader update PR so compatibility is reviewed together.
5. The normal repository CI remains the merge gate for any generated update PR.
6. The exact-source bgutil PO-token provider remains independently pinned and must keep passing its existing source/runtime compatibility checks.

This closes the stale-downloader failure mode without exchanging it for an unreviewed moving dependency in production.
