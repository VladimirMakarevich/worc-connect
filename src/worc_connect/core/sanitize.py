"""Making a stranger's text safe to put in a task file worc will accept without repairing it.

worc's gate rejects; it does not sanitize. A title carrying an argv-shaped token quarantines the
task, and a body over one of three size limits does the same — and from outside worc's home a
quarantine looks like a task that simply vanished. So everything that could trip either check is
dealt with here, deterministically, before the file is written.

The two halves are deliberately asymmetric, because the risk is. A **title** is a front-matter
value, which worc scans, so it is reduced to plain text: no control characters, no newline, none of
the tokens the scan names, no leading dash. A **body** is never scanned — worc's own documentation
says legitimate tasks embed shell snippets — so it travels verbatim and is only ever *shortened*,
with a marker and the item's URL left where a reader will find what was cut.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

# The tokens worc's front-matter scan refuses outright. Dropped rather than escaped: the scan reads
# the parsed value, so any escaping that survived YAML would still be the offending character.
_INJECTION_TOKENS: Final = (";", "`", "|", "$(")

# Long enough to carry a real issue title, short enough that a pull-request title built from it
# stays readable. worc imposes no limit of its own; this is the connector's.
TITLE_MAX_LEN: Final = 120

_WHITESPACE: Final = re.compile(r"\s+")

# The whole leading run of dashes, the whitespace inside it included. worc strips a value and
# refuses one that then starts with `-`; its forbidden-flag check reads that same stripped value as
# one argv token, and every shape it names — `--yolo`, `--dangerously…`, `--sandbox`, `-s`,
# `--permission-mode` — begins with a dash too, so a title whose first character is anything else
# can match none of them. Whitespace belongs in the run because `- -foo` with only its first dash
# removed is `-foo`: the same refusal, one step later.
_LEADING_DASHES: Final = re.compile(r"^[-\s]+")

# What a reader sees where the item's own text was cut. The wording is the connector's own, and it
# carries the URL so the full text is one click away.
TRUNCATION_NOTICE: Final = "…truncated by worc-connect — the full text is on the item: "


def sanitize_title(raw: str, *, fallback: str) -> str:
    """``raw`` reduced to a front-matter value worc's injection scan accepts, or ``fallback``.

    ``fallback`` is used whenever nothing survives — a title made entirely of punctuation, or an
    item with no title at all — because worc requires a non-blank one and a task the connector
    could not name is still a task the operator asked for.
    """
    text = "".join(character for character in raw if not _is_control(character))
    for token in _INJECTION_TOKENS:
        text = text.replace(token, " ")
    collapsed = _WHITESPACE.sub(" ", text).strip()
    trimmed = _LEADING_DASHES.sub("", collapsed)
    if len(trimmed) > TITLE_MAX_LEN:
        trimmed = trimmed[:TITLE_MAX_LEN].strip()
    return trimmed or fallback


def _is_control(character: str) -> bool:
    """Whether ``character`` is a control character, newlines and tabs included.

    Tabs and newlines are legal in a Markdown body and illegal in a title, and this function is
    only ever asked about a title, so it does not carve them out.
    """
    return unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}


def truncate_body(
    body: str, *, max_bytes: int, max_lines: int, max_line_bytes: int, url: str
) -> str:
    """``body`` shortened until it fits all three of worc's limits, with a visible marker.

    Applied in the order the limits interact: over-long lines are cut first (one line can carry the
    whole file), then the line count, then the byte total, so each step works on text the previous
    one already bounded. Nothing is re-encoded and nothing is reflowed — a body that already fits is
    returned unchanged, byte for byte.
    """
    notice = f"{TRUNCATION_NOTICE}{url}"
    lines = [_fit_line(line, max_line_bytes, notice) for line in body.splitlines()]
    if len(lines) > max_lines:
        # One line of the budget is spent on the marker, so the result is still inside the limit.
        lines = [*lines[: max(max_lines - 1, 0)], notice]
    text = "\n".join(lines)
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    return _fit_bytes(text, max_bytes, notice)


def _fit_line(line: str, max_line_bytes: int, notice: str) -> str:
    """One line cut to ``max_line_bytes``, ending in the marker when anything was dropped."""
    if len(line.encode("utf-8")) <= max_line_bytes:
        return line
    room = max(max_line_bytes - len(notice.encode("utf-8")), 0)
    return f"{_clip(line, room)}{notice}"


def _fit_bytes(text: str, max_bytes: int, notice: str) -> str:
    """``text`` cut to ``max_bytes`` including the marker appended on its own line."""
    tail = f"\n{notice}"
    room = max(max_bytes - len(tail.encode("utf-8")), 0)
    return f"{_clip(text, room)}{tail}"


def _clip(text: str, max_bytes: int) -> str:
    """The longest prefix of ``text`` that encodes to at most ``max_bytes``.

    Cut on the encoded form and decoded back with the incomplete tail discarded, so a multi-byte
    character is never split — worc rejects a file that is not valid UTF-8, which would turn a
    truncation into a quarantine.
    """
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
