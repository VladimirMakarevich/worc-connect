"""The GitHub adapter: a thin wrapper over the operator's own ``gh`` login.

Every call is an argument list pinned with ``--repo OWNER/REPO`` from configuration; comment bodies
travel through ``--body-file``; the adapter holds no token of its own.
"""

from __future__ import annotations
