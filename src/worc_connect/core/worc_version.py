"""Which worc the connector is talking to, and therefore which contract it may use.

There is exactly one thing the connector has to know about the worc on the host before it writes a
task file: whether that worc understands ``references:``. An unknown front-matter key is a hard
reject at worc's gate, and a rejected task is a silent quarantine inside a home the connector may
not look in — so the key is emitted only against a worc that is known to accept it, and against
anything else the task is built exactly as it would have been without the contract.

The version grammar lives here, away from the launcher, because it is pure text and the rule it
encodes is worth reading on its own: a development build of a release is **older** than that
release, which is what makes an unreleased build of the contract fail closed rather than open.
"""

from __future__ import annotations

import re
from typing import Final

# The first worc release that ships `references:`, `pr_url` and the `rejected` section. Stated as
# text because it is also what the operator reads in the connector's documentation, and two
# spellings of one number is one too many.
MINIMUM_VERSION: Final = "0.14.0a1"

# `<name> <version>`, as every argparse `--version` action renders it. The version itself is PEP-440
# shaped: a release triple, an optional pre-release, an optional development suffix and an optional
# local segment, which is what a build from a checkout carries (`0.14.0a1.dev7+gabc1234`).
_VERSION_PATTERN: Final = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<micro>\d+)"
    r"(?:(?P<pre>a|b|rc)(?P<pre_number>\d+))?"
    r"(?P<dev>\.dev\d+)?"
    r"(?:\+.*)?$"
)

# How the pre-release segments order among themselves and against a final release.
_PRE_RANKS: Final = {"a": 0, "b": 1, "rc": 2}
_FINAL_RANK: Final = 3

type _Key = tuple[int, int, int, int, int, int]


def supports_contract(reported: str) -> bool:
    """Whether the worc that printed ``reported`` is at least :data:`MINIMUM_VERSION`.

    ``reported`` is whatever ``worc --version`` wrote, including an empty string for a worc that
    could not be asked at all. Anything this grammar does not recognise answers ``False``: the
    question is "may the connector add a key to a task file", and the only safe answer to "I cannot
    tell" is the one that keeps the file identical to what an older worc already accepts.
    """
    found = _parse(reported)
    minimum = _parse(MINIMUM_VERSION)
    if found is None or minimum is None:  # pragma: no cover - the constant always parses
        return False
    return found >= minimum


def _parse(reported: str) -> _Key | None:
    """The sortable key of the version inside ``reported``, or ``None`` for text without one."""
    tokens = reported.split()
    matched = _VERSION_PATTERN.match(tokens[-1]) if tokens else None
    if matched is None:
        return None
    pre = matched.group("pre")
    return (
        int(matched.group("major")),
        int(matched.group("minor")),
        int(matched.group("micro")),
        _FINAL_RANK if pre is None else _PRE_RANKS[pre],
        int(matched.group("pre_number") or 0),
        # A development build precedes the release it is building toward, so it sorts below a
        # version that is otherwise identical.
        0 if matched.group("dev") else 1,
    )
