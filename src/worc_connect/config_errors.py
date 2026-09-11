"""The one exception the configuration layer raises, in the module both halves may import.

Its own file so the reader and the schema can each raise it without either importing the other:
the reader needs it for a key it cannot read, and the rules need it for a value they refuse.
"""

from __future__ import annotations


class ConfigError(Exception):
    """A configuration the connector refuses to run with; the message names the offending key."""
