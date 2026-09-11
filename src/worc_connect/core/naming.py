"""Task ids and branch names: the two names the connector allocates and then has to live with.

Both are allocated here and nowhere else, because both are load-bearing beyond the file they appear
in. The id is the connector's idempotency key — it is never reused, and a re-trigger takes the next
sequence suffix rather than the previous name — and the branch is how a pull request is found the
first time, which is what makes the whole follow-through possible.

Both are also values worc's gate rejects rather than repairs, so they are valid by construction:
the id follows worc's grammar (including the trailing-dot and Windows device-name rules the
configuration loader already applies to the prefix), and the branch is a ref name that fits the
50-character budget above which worc discards it and generates its own — which the connector could
then not search for.
"""

from __future__ import annotations

import re
from typing import Final

# The characters a branch component may carry. Everything outside it — whitespace, the ref
# metacharacters `~^:?*[\`, punctuation a shell or a CLI parser could read as anything — collapses
# into a separator, so a slug cannot smuggle a token into a ref name.
_SLUG_ALLOWED: Final = re.compile(r"[^a-z0-9]+")

# worc discards a longer branch name and generates its own, which the connector could not then find
# a pull request by. The whole ref, prefix included, is held to it.
BRANCH_MAX_LEN: Final = 50

# A component that ends in a dot, or in `.lock`, is not a legal ref; trimming the slug to fit the
# budget is the operation that can produce one, so the tail is cleaned after every trim.
_SLUG_TRAILING: Final = "-."


def allocate_task_id(prefix: str, item_id: str, seq: int) -> str:
    """The id of attempt ``seq`` at ``item_id``: ``gh-142`` first, then ``gh-142.2``.

    The first attempt carries no suffix so the common case reads as the item it came from. worc's
    grammar admits the dot, and its duplicate-id rule is what the suffix exists to respect: an id
    is never reused, not even after the previous attempt reached a terminal state.
    """
    base = f"{prefix}-{item_id}"
    return base if seq <= 1 else f"{base}.{seq}"


def allocate_branch(prefix: str, task_id: str, title: str) -> str:
    """The branch worc will publish ``task_id`` from: ``<prefix>/<task-id>-<slug>``.

    The slug is a courtesy to whoever reads the branch list; the id is what makes the name unique,
    so the slug is the part that gives way when the budget is tight, and the name stays valid with
    no slug at all. The composed ref always carries the ``<prefix>/`` segment, which is also what
    keeps it from ever colliding with a base branch — those are single-segment names.
    """
    stem = f"{prefix}/{task_id}"
    slug = slugify(title)
    if not slug:
        return stem
    room = BRANCH_MAX_LEN - len(stem) - 1
    if room <= 0:
        return stem
    trimmed = slug[:room].strip(_SLUG_TRAILING)
    return f"{stem}-{trimmed}" if trimmed else stem


def slugify(title: str) -> str:
    """``title`` as the lower-case, hyphen-separated tail of a branch name, or an empty string.

    Lossy on purpose: everything that is not an ASCII letter or digit becomes a separator, so a
    title in any script, or one made entirely of punctuation, yields either a plain slug or nothing
    at all — never a fragment that would have to be validated again downstream.
    """
    return _SLUG_ALLOWED.sub("-", title.casefold()).strip(_SLUG_TRAILING)
