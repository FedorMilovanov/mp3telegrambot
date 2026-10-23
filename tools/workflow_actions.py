"""Parse ``uses: owner/action@ref`` lines out of GitHub Actions workflows.

Why this module exists
----------------------
Two tests asserted ``actions/checkout@v6`` and ``actions/setup-python@v6`` as
literal strings. The intent was legitimate — those majors are the ones that run
on a supported Node runtime — but equality turned every Dependabot bump of an
action into a red build:

    ``actions/checkout@v6`` -> ``v7``  =>  "Run tests" fails on 3 Pythons

The automation was right and the assertion was wrong. A floor (``>= 6``)
expresses the same requirement and survives future majors.

Hard-coded action versions are worth avoiding for the same reason we avoid
hard-coded model ids: the bump must be a one-place change, and the test must
still be able to say "this is too old".
"""
from __future__ import annotations

import re
from typing import Final

# ``uses:`` may be the first key of a list item, indented, quoted, or followed
# by a comment; anything after ``@`` is a ref (``v6``, ``v6.1.0`` or a sha).
USES_RE: Final[re.Pattern[str]] = re.compile(
    r"^[ \t]*(?:-[ \t]+)?uses:[ \t]*[\"']?(?P<action>[\w.\-/]+)@(?P<ref>[^\"'\s#]+)",
    re.MULTILINE,
)
_TAG_MAJOR_RE: Final[re.Pattern[str]] = re.compile(r"^v(?P<major>\d+)(?:\.|$)")

# Node 20 action runtimes are deprecated by GitHub, so these majors are the
# minimum that still runs on a supported Node version. Raise a number here when
# the floor moves; never compare against an exact tag.
MINIMUM_ACTION_MAJORS: Final[dict[str, int]] = {
    "actions/checkout": 6,
    "actions/download-artifact": 4,
    "actions/github-script": 7,
    "actions/setup-node": 4,
    "actions/setup-python": 6,
    "actions/upload-artifact": 4,
}


def action_refs(text: str) -> dict[str, set[str]]:
    """Map action name to every ref used in ``text``."""
    refs: dict[str, set[str]] = {}
    for match in USES_RE.finditer(text):
        refs.setdefault(match.group("action"), set()).add(match.group("ref"))
    return refs


def action_major(ref: str) -> int | None:
    """Major of a tag ref, or ``None`` for a sha-pinned ref."""
    match = _TAG_MAJOR_RE.match(ref)
    return int(match.group("major")) if match else None


def minimum_major(text: str, action: str) -> int | None:
    """Lowest major used for ``action``; ``None`` when only shas are pinned."""
    majors = [action_major(ref) for ref in action_refs(text).get(action, ())]
    known = [major for major in majors if major is not None]
    return min(known) if known else None


def below_floor(text: str) -> list[str]:
    """Human-readable violations of :data:`MINIMUM_ACTION_MAJORS`."""
    problems: list[str] = []
    for action, refs in sorted(action_refs(text).items()):
        floor = MINIMUM_ACTION_MAJORS.get(action)
        if floor is None:
            continue
        for ref in sorted(refs):
            major = action_major(ref)
            if major is not None and major < floor:
                problems.append(f"{action}@{ref} is below the v{floor} floor")
    return problems
