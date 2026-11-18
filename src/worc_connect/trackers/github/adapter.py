"""The GitHub adapter: everything the connector does to an issue, and nothing else.

Nothing above this module knows that GitHub calls a work item an issue, that its identifier is a
number, or that a state is a label. Both directions of that mapping live in this package: the reads
that produce a normalized item, reply or pull request are :mod:`.reads`, and this module adds the
writes that publish a connector state, post a comment and close an issue.

Two disciplines are visible in the call shapes and are not negotiable. A comment body travels as a
**file**, never as an argument — an issue is written by strangers and the habit of putting text in
argv is one worth never starting. And the identifier is proven to be a number before it reaches an
argument list, which is what makes it the one item-derived value the connector ever passes to a
command.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from worc_connect.core.items import ItemState
from worc_connect.trackers.base import TrackerUnavailable
from worc_connect.trackers.github.gh import GhCommand
from worc_connect.trackers.github.payloads import entries
from worc_connect.trackers.github.reads import GitHubReads, issue_number

# Repositories rarely carry more than a few dozen labels, and the listing is read once per process
# to find which of the connector's own are missing. The cap is an optimisation, not a limit: a label
# the capped listing hid is created anyway, and the tracker's "already exists" is read as present.
_LABEL_PAGE_LIMIT: Final = 200

# What a label the connector created says about itself. Fixed text, because a label description is
# repository furniture an operator may edit freely afterwards. The trigger label gets its own: it is
# the one maintainers apply by hand, so it should say what applying it does.
_LABEL_DESCRIPTION: Final = "Managed by worc-connect"
_TRIGGER_DESCRIPTION: Final = "Hands this issue to worc (worc-connect trigger label)"

# How `gh label create` reports a name that is already taken. Read as the answer it is rather than
# as a failure: a label that exists is exactly the state wanted, and treating it as an error would
# abort every tick for good on a repository whose label listing the cap truncated.
_ALREADY_EXISTS: Final = "already exists"


@dataclass(frozen=True)
class GitHubAdapter(GitHubReads):
    """The GitHub calls the core performs — the reads it inherits, and the writes below."""

    def set_state(self, identifier: str, state: ItemState, *, previous: ItemState | None) -> None:
        """Move the issue to ``state`` in one edit, so it never carries two states or none."""
        arguments = ["issue", "edit", issue_number(identifier)]
        if previous is not None and previous is not state:
            arguments += ["--remove-label", self._label_for(previous)]
        arguments += ["--add-label", self._label_for(state)]
        self.command.run(*arguments)

    def comment(self, identifier: str, body_path: Path) -> None:
        """Post a comment whose body is read from a file, never composed into an argument."""
        self.command.run(
            "issue", "comment", issue_number(identifier), "--body-file", str(body_path)
        )

    def close(self, identifier: str) -> None:
        """Close the issue, with no text of its own.

        The closing message is posted before this as an ordinary comment, through a body file: an
        issue the code host already closed is reported "already closed" by ``gh``, which then posts
        nothing at all, so a message carried by the close would be lost exactly when a pull request
        with a closing keyword was merged.
        """
        self.command.run("issue", "close", issue_number(identifier))

    def ensure_labels(self, states: tuple[ItemState, ...], *, triggers: tuple[str, ...]) -> None:
        """Create the labels the repository is missing, and leave the ones it has alone.

        Existing labels are never edited: their colour and description belong to whoever set them
        up, and a connector that reset them on every start would be fighting the maintainers. The
        trigger labels are configuration the operator wrote, which is what allows their names in an
        argument list at all. Names compare case-insensitively, as GitHub keeps them unique.
        """
        payload = self.command.read_json(
            "label", "list", "--limit", str(_LABEL_PAGE_LIMIT), "--json", "name"
        )
        present = {
            entry["name"].casefold()
            for entry in entries(payload)
            if isinstance(entry.get("name"), str)
        }
        wanted = [(self._label_for(state), _LABEL_DESCRIPTION) for state in states]
        wanted += [(name, _TRIGGER_DESCRIPTION) for name in triggers]
        for name, description in wanted:
            if name.casefold() not in present:
                self._create_label(name, description)

    def _create_label(self, name: str, description: str) -> None:
        """Create one label, reading "already exists" as the label being there after all."""
        try:
            self.command.run("label", "create", name, "--description", description)
        except TrackerUnavailable as exc:
            if _ALREADY_EXISTS not in str(exc).casefold():
                raise


def build_adapter(*, repo: str, labels_prefix: str) -> GitHubAdapter:
    """Build the adapter the ``worc_connect.trackers`` entry point for ``github`` resolves to."""
    return GitHubAdapter(command=GhCommand(repo=repo), labels_prefix=labels_prefix)
