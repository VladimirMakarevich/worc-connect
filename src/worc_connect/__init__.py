"""worc-connect — a tracker connector for wastech-orchestrator (worc).

Watches an issue tracker for one repository, turns each work item a maintainer has explicitly gated
into a worc task through worc's own ingress (``tasks/preparing/`` + ``worc promote``), and writes
the outcome back to the tracker. The core knows no tracker API; adapters live under ``trackers/``
behind optional dependencies and are discovered through the ``worc_connect.trackers`` entry-point
group.
"""

from __future__ import annotations

__version__ = "0.1.0.dev0"
