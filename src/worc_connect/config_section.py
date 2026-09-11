"""The typed reader the configuration loader reads every key through.

Its whole job is the error message. Every accessor names the **dotted path** of the offending key
and raises :class:`~worc_connect.config.ConfigError`, which is why the loader reads through an
object instead of ``dict.get``: an operator who mistypes a key learns which one, and the process
stops instead of running with a default nobody chose. A separate module because it is a reader over
YAML mappings and knows nothing about what the connector's keys mean — the schema and the rules
live next door.
"""

from __future__ import annotations

from typing import Any

from worc_connect.config_errors import ConfigError


class Section:
    """A typed, key-naming reader over one mapping of the configuration file.

    Every accessor raises :class:`ConfigError` naming the dotted path of the offending key, which
    is the whole reason the reads go through an object rather than ``dict.get``: an operator who
    mistypes a key learns which one, and the process stops instead of running with a default nobody
    chose.
    """

    def __init__(self, data: Any, prefix: str = "") -> None:
        if not isinstance(data, dict):
            raise ConfigError(f"{self._describe(prefix)} must be a mapping")
        self._data: dict[str, Any] = data
        self._prefix = prefix

    @staticmethod
    def _describe(prefix: str) -> str:
        """How this section is named in an error message."""
        return f"`{prefix}`" if prefix else "the configuration file"

    def _key(self, name: str) -> str:
        """The dotted path of ``name`` as the operator wrote it in the file."""
        return f"{self._prefix}.{name}" if self._prefix else name

    def reject_unknown(self, *known: str) -> None:
        """Refuse any key this build does not declare, so a typo cannot pass as a default."""
        for name in sorted(self._data):
            if name not in known:
                raise ConfigError(f"unknown configuration key `{self._key(name)}`")

    def section(self, name: str) -> Section:
        """A reader over the nested mapping at ``name``; an absent section reads as empty."""
        return Section(self._data.get(name, {}), self._key(name))

    def string(self, name: str, default: str | None = None) -> str:
        """A non-blank string; ``default`` applies when absent, otherwise the key is required."""
        value = self.optional_string(name)
        if value is not None:
            return value
        if default is None:
            raise ConfigError(f"`{self._key(name)}` is required")
        return default

    def optional_string(self, name: str) -> str | None:
        """A non-blank string, or ``None`` when the key is absent — which means "do not emit it"."""
        if name not in self._data:
            return None
        value = self._data[name]
        if isinstance(value, bool):
            # YAML's own vocabulary overlaps ours: a bare `off`, `no`, `on` or `yes` parses as a
            # boolean, so the value an operator is most likely to type for a named mode is exactly
            # the one that does not survive the parser. Say which mistake it is.
            raise ConfigError(
                f"`{self._key(name)}` reads as a boolean — YAML parses a bare `off`/`on`/"
                f'`no`/`yes` that way. Quote the value: `{name}: "off"`.'
            )
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"`{self._key(name)}` must be a non-empty string")
        return value.strip()

    def flag(self, name: str, default: bool) -> bool:
        """A boolean; a value that is not a real boolean is an error, never a truthiness test."""
        value = self.optional_flag(name)
        return default if value is None else value

    def optional_flag(self, name: str) -> bool | None:
        """A boolean, or ``None`` when the key is absent."""
        if name not in self._data:
            return None
        value = self._data[name]
        if not isinstance(value, bool):
            raise ConfigError(f"`{self._key(name)}` must be true or false")
        return value

    def positive_int(self, name: str, default: int) -> int:
        """An integer above zero; a boolean is rejected even though Python counts it as an int."""
        if name not in self._data:
            return default
        value = self._data[name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ConfigError(f"`{self._key(name)}` must be a positive integer")
        return value

    def string_list(self, name: str) -> tuple[str, ...]:
        """A list of non-blank strings; an absent key reads as the empty list."""
        if name not in self._data:
            return ()
        value = self._data[name]
        if not isinstance(value, list):
            raise ConfigError(f"`{self._key(name)}` must be a list of strings")
        entries: list[str] = []
        for entry in value:
            if not isinstance(entry, str) or not entry.strip():
                raise ConfigError(f"`{self._key(name)}` must contain non-empty strings only")
            entries.append(entry.strip())
        return tuple(entries)

    def string_map(self, name: str) -> dict[str, str]:
        """A mapping of non-blank strings to non-blank strings; an absent key reads as empty."""
        if name not in self._data:
            return {}
        value = self._data[name]
        if not isinstance(value, dict):
            raise ConfigError(f"`{self._key(name)}` must be a mapping of strings to strings")
        mapping: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str) or not isinstance(raw_value, str):
                raise ConfigError(f"`{self._key(name)}` must map strings to strings")
            if not raw_key.strip() or not raw_value.strip():
                raise ConfigError(f"`{self._key(name)}` must not contain empty keys or values")
            mapping[raw_key.strip()] = raw_value.strip()
        return mapping
