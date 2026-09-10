"""Tracker adapters: one subpackage per tracker, behind an optional dependency.

``base`` holds the ``TrackerAdapter`` protocol and the infrastructure error classes the core reacts
to; a concrete adapter registers itself under the ``worc_connect.trackers`` entry-point group and is
resolved by name from the composition root, never imported by the core.
"""

from __future__ import annotations
