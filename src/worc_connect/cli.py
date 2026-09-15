"""Command-line entry point — the composition root.

``init`` creates the connector's home, ``watch`` runs the loop (once or as a daemon, really or as a
dry run), ``status`` reports what the connector believes, and ``install-flow`` delivers the triage
flow this package ships into worc's own flow directory. This is the only module that resolves a
tracker adapter by name: it looks the configured tracker up in the ``worc_connect.trackers``
entry-point group and hands the loop whatever it finds, which is why no module below it imports a
concrete adapter.

Exit codes are part of the interface: ``0`` a completed run, ``1`` an infrastructure failure or a
refused start, ``2`` a usage or configuration problem. A configuration the loader will not accept
never reaches the loop — the process stops before a single tracker call is made, because a connector
that guessed at its own gate could turn an item nobody gated into a task.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from importlib.metadata import entry_points
from pathlib import Path

from worc_connect import __version__, flows
from worc_connect import config as config_module
from worc_connect.config import ConfigError, ConnectorConfig
from worc_connect.core.loop import Action, TickReport, Watcher
from worc_connect.core.state import (
    META_LAST_TICK_AT,
    META_LAST_TICK_RESULT,
    META_WATERMARK,
    ItemRow,
    StateStore,
)
from worc_connect.core.worc_cli import WorcCommand, WorcUnavailable
from worc_connect.core.worc_version import MINIMUM_VERSION
from worc_connect.home import HOME_DIRNAME, ConnectorHome, ensure_gitignore_entry, render_config
from worc_connect.trackers.base import AdapterFactory, TrackerAdapter

ENTRY_POINT_GROUP = "worc_connect.trackers"

_EXIT_OK = 0
_EXIT_FAILED = 1
_EXIT_USAGE = 2

_LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


def build_parser() -> argparse.ArgumentParser:
    """The argument parser, built once so ``--help`` and the tests read the same surface."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--home",
        metavar="PATH",
        help=f"the connector's home directory (default: ./{HOME_DIRNAME})",
    )

    parser = argparse.ArgumentParser(
        prog="worc-connect",
        description="Tracker connector for wastech-orchestrator: gated items in, worc tasks out.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subcommands = parser.add_subparsers(dest="command", metavar="COMMAND")

    init = subcommands.add_parser(
        "init",
        parents=[common],
        help="create the connector's home and a configuration to edit",
    )
    init.add_argument("--repo", required=True, metavar="OWNER/REPO", help="the repository to watch")
    init.add_argument("--tracker", default="github", help="the tracker adapter to use")
    init.set_defaults(handler=_cmd_init)

    watch = subcommands.add_parser(
        "watch", parents=[common], help="poll the tracker and hand gated items to worc"
    )
    watch.add_argument("--once", action="store_true", help="run a single pass instead of a loop")
    watch.add_argument(
        "--dry-run",
        action="store_true",
        help="print what the tick would do and write nothing at all",
    )
    watch.set_defaults(handler=_cmd_watch)

    status = subcommands.add_parser(
        "status", parents=[common], help="report the known items, the watermark and the last tick"
    )
    status.set_defaults(handler=_cmd_status)

    install_flow = subcommands.add_parser(
        "install-flow",
        parents=[common],
        help="copy the shipped triage flow into worc's .worc/flows/",
    )
    install_flow.add_argument(
        "--force",
        action="store_true",
        help="overwrite a copy you have edited (without this, an edited file is left alone)",
    )
    install_flow.set_defaults(handler=_cmd_install_flow)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_usage()
        return _EXIT_USAGE
    exit_code: int = handler(args)
    return exit_code


def _home(args: argparse.Namespace) -> ConnectorHome:
    """The home the command operates on: ``--home`` when given, else the one in this directory."""
    override = getattr(args, "home", None)
    if override:
        return ConnectorHome(Path(override))
    return ConnectorHome.beside(Path.cwd())


def _cmd_init(args: argparse.Namespace) -> int:
    """Create the home, write a configuration to edit, and keep the home out of git."""
    if not config_module.is_valid_repo(args.repo):
        print("worc-connect: --repo must name one repository as OWNER/REPO", file=sys.stderr)
        return _EXIT_USAGE
    home = _home(args)
    if home.config_path.exists():
        print(
            f"worc-connect: {home.config_path.as_posix()} already exists; edit it instead",
            file=sys.stderr,
        )
        return _EXIT_FAILED
    home.path.mkdir(parents=True, exist_ok=True)
    home.config_path.write_text(
        render_config(tracker=args.tracker, repo=args.repo), encoding="utf-8", newline=""
    )
    print(f"init: wrote {home.config_path.as_posix()}")
    if ensure_gitignore_entry(home.clone_path):
        print(f"init: added {HOME_DIRNAME}/ to {home.clone_path.as_posix()}/.gitignore")
    print("init: review the gate and the dispatch fields, then run")
    print("init:   worc-connect watch --once --dry-run")
    return _EXIT_OK


def _cmd_watch(args: argparse.Namespace) -> int:
    """Run the loop against the configured tracker, as a single pass or as a daemon."""
    home = _home(args)
    try:
        config = config_module.load(home.config_path)
        adapter = _resolve_adapter(config)
    except ConfigError as exc:
        print(f"worc-connect: {exc}", file=sys.stderr)
        return _EXIT_USAGE
    _configure_logging(home, dry_run=args.dry_run)
    store = _open_store(home, read_only=args.dry_run)
    try:
        watcher = Watcher(
            config=config,
            adapter=adapter,
            store=store,
            home=home,
            worc=WorcCommand(command=config.worc.command, repo_path=config.worc.repo_path),
            on_tick=_reporter(config, dry_run=args.dry_run),
        )
        return watcher.run(once=args.once, dry_run=args.dry_run)
    finally:
        store.close()


def _cmd_status(args: argparse.Namespace) -> int:
    """Report the rows, their phases, the watermark and how the last tick ended."""
    home = _home(args)
    try:
        config = config_module.load(home.config_path)
    except ConfigError as exc:
        print(f"worc-connect: {exc}", file=sys.stderr)
        return _EXIT_USAGE
    store = _open_store(home, read_only=True)
    try:
        print(f"status: tracker={config.tracker} repo={config.repo} home={home.path.as_posix()}")
        print(f"status: watermark={store.meta(META_WATERMARK) or '-'}")
        print(
            f"status: last_tick={store.meta(META_LAST_TICK_AT) or '-'} "
            f"result={store.meta(META_LAST_TICK_RESULT) or '-'}"
        )
        rows = store.rows()
        if not rows:
            print("status: no items taken on yet")
        for row in rows:
            print(f"status: {_row_line(row)}")
    finally:
        store.close()
    return _EXIT_OK


def _cmd_install_flow(args: argparse.Namespace) -> int:
    """Deliver the shipped triage flow into worc's flow directory, on an explicit command only.

    Three things are checked before anything is written, and each of them is a reason the flow
    would be installed and then not work: the analysis step has to be switched on, the configured
    flow has to be the one this package ships, and the installed worc has to understand the
    ``report_dir`` key the flow declares — without it worc refuses the flow at load, and the
    operator would learn that from a task that failed rather than from the command that installed
    it.
    """
    home = _home(args)
    try:
        config = config_module.load(home.config_path)
    except ConfigError as exc:
        print(f"worc-connect: {exc}", file=sys.stderr)
        return _EXIT_USAGE
    if not config.research.in_worc:
        print(
            "worc-connect: `research.mode` is not `worc`, so nothing needs this flow; set it and "
            "run install-flow again",
            file=sys.stderr,
        )
        return _EXIT_USAGE
    if config.research.flow != flows.PACKAGED_FLOW:
        print(
            f"worc-connect: `research.flow` names `{config.research.flow}`, and this package ships "
            f"`{flows.PACKAGED_FLOW}` — a flow of your own is yours to install",
            file=sys.stderr,
        )
        return _EXIT_USAGE
    if not _worc_understands_the_flow(config):
        print(
            f"worc-connect: the worc in this clone predates `flow.report_dir`, which this flow "
            f"declares, and would refuse it at load; worc {MINIMUM_VERSION} or newer is needed",
            file=sys.stderr,
        )
        return _EXIT_FAILED
    return _report_installation(
        flows.install(flows.flows_dir(config.worc.repo_path), force=args.force)
    )


def _worc_understands_the_flow(config: ConnectorConfig) -> bool:
    """Whether the installed worc knows the flow key this flow depends on.

    The same handshake the task builder uses, against the same minimum: the release that ships
    ``references:`` is the one that ships ``report_dir``, so one version answers both questions.
    """
    worc = WorcCommand(command=config.worc.command, repo_path=config.worc.repo_path)
    try:
        return worc.accepts_references()
    except WorcUnavailable:  # pragma: no cover - the handshake answers rather than raising
        return False


def _report_installation(installed: flows.Installed) -> int:
    """Print what the install did, file by file, and fail when an edited copy was left alone."""
    for name, outcome in installed.results:
        print(f"install-flow: {outcome} {name}")
    if not installed.refused:
        print("install-flow: worc will pick the flow up on its next task")
        return _EXIT_OK
    print(
        "worc-connect: the files above are not the ones this package ships — re-run with --force "
        "to replace your edits",
        file=sys.stderr,
    )
    return _EXIT_FAILED


def _row_line(row: ItemRow) -> str:
    """One cached row as a single line: identifiers and phase only, never the item's own text."""
    pull_request = f"#{row.pr_number} {row.pr_url}" if row.pr_number else "-"
    return (
        f"item={row.item_id} seq={row.seq} phase={row.phase} "
        f"task={row.task_id or '-'} branch={row.branch or '-'} pr={pull_request}"
    )


def _open_store(home: ConnectorHome, *, read_only: bool) -> StateStore:
    """The state store, opened so that a reporting command cannot create or change the database."""
    if read_only:
        return StateStore.read_only(home.state_path)
    return StateStore(home.state_path)


def _resolve_adapter(config: ConnectorConfig) -> TrackerAdapter:
    """The adapter the configured tracker names, resolved through the entry-point group.

    A tracker nobody installed an adapter for is a configuration error naming the extra to install,
    not a stack trace: the group is the whole extension mechanism, so "not found" is the expected
    answer for a tracker whose optional dependency was never selected.
    """
    matches = entry_points(group=ENTRY_POINT_GROUP).select(name=config.tracker)
    if not matches:
        installed = sorted(point.name for point in entry_points(group=ENTRY_POINT_GROUP))
        available = ", ".join(installed) if installed else "none"
        raise ConfigError(
            f"`tracker` names `{config.tracker}`, for which no adapter is installed — try "
            f'`pip install "worc-connect[{config.tracker}]"` (installed adapters: {available})'
        )
    factory: AdapterFactory = next(iter(matches)).load()
    return factory(repo=config.repo, labels_prefix=config.write_back.labels_prefix)


def _configure_logging(home: ConnectorHome, *, dry_run: bool) -> None:
    """Send the action log to stderr, and to the connector's own log file unless this is a dry run.

    A dry run writes nothing anywhere, log file included: the promise is about the filesystem, not
    only about the task folder, and an operator's first safe command should leave no trace at all.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if not dry_run:
        home.path.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(home.log_path, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, handlers=handlers, force=True)


def _label(config: ConnectorConfig, state: str | None) -> str:
    """The label a dry run says it would put on an item, spelled as the operator configured it."""
    return "-" if state is None else f"{config.write_back.labels_prefix}{state}"


def _reporter(config: ConnectorConfig, *, dry_run: bool) -> Callable[[TickReport], None]:
    """The per-tick printer: a plan an operator can act on for a dry run, a summary otherwise."""

    def report(tick: TickReport) -> None:
        summary = (
            f"repo={config.repo} tracker={config.tracker} listed={tick.listed} "
            f"followed={tick.followed} gated={tick.counted(Action.STAGE)} "
            f"following={tick.counted(Action.FOLLOW)}"
        )
        if not dry_run:
            print(f"tick: {summary}")
            return
        print("dry run: nothing is written — no task file, no state row, no tracker call")
        print(f"plan: {summary}")
        for planned in tick.actions:
            print(
                f"plan: item={planned.item_id} action={planned.action} "
                f"reason={planned.reason} task={planned.task_id or '-'} "
                f"branch={planned.branch or '-'} "
                f"label={_label(config, planned.state)} url={planned.url}"
            )
        print(f"plan: watermark={tick.watermark.isoformat() if tick.watermark else '-'}")

    return report
