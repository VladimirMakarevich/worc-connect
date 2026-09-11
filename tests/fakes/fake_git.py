#!/usr/bin/env python
"""A ``git`` that records being launched and refuses to do anything.

The connector must never run git in the operator's clone — worc owns every git operation there, and
the connector's only write is an untracked file in worc's staging area. That is asserted from this
recording rather than from the host: a test that merely observed "the repository looks unchanged"
would pass just as well against a connector that ran ``git status``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CALLS_FILENAME = "calls.jsonl"
HOME_VARIABLE = "WORC_CONNECT_FAKE_GIT_HOME"


def main(argv: list[str]) -> int:
    """Record the call and fail; nothing in the connector may depend on git succeeding."""
    home = Path(os.environ[HOME_VARIABLE])
    with (home / CALLS_FILENAME).open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(argv) + "\n")
    sys.stderr.write("fake git: the connector may not run git in the clone\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
