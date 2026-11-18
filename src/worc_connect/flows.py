"""The triage flow this package ships, and the one place it is allowed to write into worc's home.

Installing a flow is the single exception to "the connector never writes inside ``.worc/``", and it
is narrow by construction: it happens only on an explicit operator command, it writes only under
``.worc/flows/``, and it writes only the bytes shipped inside this distribution. Nothing here reads
worc's configuration, its database, its logs or its quarantine, and nothing here edits a file the
connector did not ship.

**An operator's copy is theirs.** A file already on disk that differs from the shipped one is an
edit somebody made on purpose, and it is refused rather than overwritten unless the operator says
otherwise in so many words. A file that is byte-identical to the shipped one, or to what an earlier
release of this package shipped, is not an edit: the first is left as it is, the second is replaced
— which is what makes running the command again a safe way to pick up a newer flow.
"""

from __future__ import annotations

import hashlib
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

# Every earlier shipped version of a file, by the SHA-256 of its bytes. A copy matching one is what
# an earlier release installed, not an edit, so it is replaced without `--force`; a copy an operator
# edited matches none and is still refused. When a shipped file changes, the hash of the version it
# replaces is added here — forgetting to is safe, it only makes the upgrade ask for `--force`.
_EARLIER_RELEASES: Final[dict[str, frozenset[str]]] = {
    f"{PACKAGED_FLOW}.yaml": frozenset(
        {"8de2ba212fd7c2651ce6a871c8f1a40f9d714c9eba77982e835403966dcfbe6f"}
    ),
    f"{PACKAGED_FLOW}/analysis.md": frozenset(
        {"9011850fdae78abf41c3984a41121d200b74ad96f6efbf1392e7adb7435c8d6d"}
    ),
    f"{PACKAGED_FLOW}/report.md": frozenset(
        {"14c4a714e0f770ba30d8694c1ef933fbc2407a8145c3be0f383d100436349d1d"}
    ),
    f"{PACKAGED_FLOW}/reproduction.md": frozenset(
        {"753fcf9696bf7af0766721df0985a09abc1f9cc8391ebde67a5e63b90e01549d"}
    ),
}


class Outcome(StrEnum):
    """What installing one file did, in the three cases an operator reacts to differently."""

    WRITTEN = "written"
    UPDATED = "updated"
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
        results.append((name, _install_one(name, destination, content, force=force)))
    return Installed(results=tuple(results))


def _install_one(name: str, destination: Path, content: bytes, *, force: bool) -> Outcome:
    """Write one file, or say why it was left alone."""
    outcome = Outcome.WRITTEN
    if destination.is_file():
        existing = destination.read_bytes()
        if existing == content:
            return Outcome.UNCHANGED
        if hashlib.sha256(existing).hexdigest() in _EARLIER_RELEASES.get(name, frozenset()):
            outcome = Outcome.UPDATED
        elif not force:
            return Outcome.REFUSED
    destination.parent.mkdir(parents=True, exist_ok=True)
    # `newline=""` is not available on a bytes write, and is not needed: the shipped bytes are what
    # worc hashes into the flow fingerprint, so they are copied exactly as they ship.
    destination.write_bytes(content)
    return outcome
