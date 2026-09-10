"""The tracker-agnostic core: gate, task builder, handoff into worc, reconcile, write-back, loop.

Everything here works on the normalized item model and the ``TrackerAdapter`` protocol; no module in
this package imports a concrete adapter (enforced by ``import-linter``).
"""

from __future__ import annotations
