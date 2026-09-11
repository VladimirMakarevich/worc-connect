"""The GitHub adapter: a thin wrapper over the operator's own ``gh`` login.

Every call is an argument list pinned with ``--repo OWNER/REPO`` from configuration; comment bodies
travel through ``--body-file``; the adapter holds no token of its own.

``build_adapter`` is what the ``worc_connect.trackers`` entry point for ``github`` resolves to, so
the composition root reaches this package by name and no module above it imports it.
"""

from __future__ import annotations

from worc_connect.trackers.github.adapter import GitHubAdapter, build_adapter

__all__ = ["GitHubAdapter", "build_adapter"]
