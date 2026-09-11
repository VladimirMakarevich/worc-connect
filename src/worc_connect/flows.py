"""The triage flow this package ships, and the one place it is allowed to write into worc's home.

Installing a flow is the single exception to "the connector never writes inside ``.worc/``", and it
is narrow by construction: it happens only on an explicit operator command, it writes only under
``.worc/flows/``, and it writes only the bytes shipped inside this distribution. Nothing here reads
worc's configuration, its database, its logs or its quarantine, and nothing here edits a file the
connector did not ship.

**An operator's copy is theirs.** A file already on disk that differs from the shipped one is an
edit somebody made on purpose, and it is refused rather than overwritten unless the operator says
otherwise in so many words. A file that is byte-identical is not an edit, so re-installing over it
is silent — which is what makes running the command again a safe way to pick up a newer flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import Final

# worc's private home and the directory inside it that holds operator flows. Fixed names, because
# they are worc's own layout and the connector reaches them through no configuration of its own.
WORC_HOME_DIRNAME: Final = ".worc"
FLOWS_DIRNAME: Final = "flows"

# The flow this distribution ships, under the name worc resolves it by: worc looks up a task's
# `task_type` as `<task_type>.yaml`, so the file name, the `flow.name` inside it and the task type
# the connector emits are all one string.
PACKAGED_FLOW: Final = "issue_triage"

_PACKAGE_FLOWS: Final = ("packaged", "flows")


class Outcome(StrEnum):
    """What installing one file did, in the three cases an operator reacts to differently."""

    WRITTEN = "written"
    UNCHANGED = "unchanged"
    REFUSED = "refused"


@dataclass(frozen=True)
class Installed:
    """What one ``install-flow`` did, per file, in the order the files were considered."""

    results: tuple[tuple[str, Outcome], ...]

    @property
    def refused(self) -> tuple[str, ...]:
        """The files an operator had edited, which is what makes the command exit non-zero."""
        return tuple(name for name, outcome in self.results if outcome is Outcome.REFUSED)


def flows_dir(repo_path: Path) -> Path:
    """worc's operator-flow directory in ``repo_path`` — the only path this module writes into."""
    return repo_path / WORC_HOME_DIRNAME / FLOWS_DIRNAME


def packaged() -> dict[str, bytes]:
    """The shipped flow and its role prompts, as the relative paths they install under.

    Read as bytes and written back as bytes: the files are worc's to parse, and a round trip
    through text decoding would be one more place line endings could change under an operator who
    checks the installed copy against the shipped one.
    """
    root = resources.files("worc_connect").joinpath(*_PACKAGE_FLOWS)
    files = {f"{PACKAGED_FLOW}.yaml": root.joinpath(f"{PACKAGED_FLOW}.yaml").read_bytes()}
    for entry in sorted(root.joinpath(PACKAGED_FLOW).iterdir(), key=lambda item: item.name):
        if entry.name.endswith(".md"):
            files[f"{PACKAGED_FLOW}/{entry.name}"] = entry.read_bytes()
    return files


def install(target: Path, *, force: bool) -> Installed:
    """Copy the shipped flow into ``target``, refusing to overwrite what an operator has edited.

    Every file is considered, so one refused prompt does not hide the state of the others: the
    operator sees the whole picture in one run and decides once whether ``--force`` is what they
    want.
    """
    results: list[tuple[str, Outcome]] = []
    for name, content in packaged().items():
        destination = target / name
        results.append((name, _install_one(destination, content, force=force)))
    return Installed(results=tuple(results))


def _install_one(destination: Path, content: bytes, *, force: bool) -> Outcome:
    """Write one file, or say why it was left alone."""
    if destination.is_file():
        existing = destination.read_bytes()
        if existing == content:
            return Outcome.UNCHANGED
        if not force:
            return Outcome.REFUSED
    destination.parent.mkdir(parents=True, exist_ok=True)
    # `newline=""` is not available on a bytes write, and is not needed: the shipped bytes are what
    # worc hashes into the flow fingerprint, so they are copied exactly as they ship.
    destination.write_bytes(content)
    return Outcome.WRITTEN
