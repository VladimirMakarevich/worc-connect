"""The connector's configuration: schema and fail-closed loader for ``.worc-connect/config.yaml``.

Shapes only — this module depends on nothing above it (enforced by ``import-linter``), so the CLI,
the core and the adapters can all read the resolved configuration without a cycle.
"""

from __future__ import annotations
