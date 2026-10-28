"""Small validation helpers shared across the package."""

from __future__ import annotations

import re

_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def require_guid(value: str | None, name: str = "id") -> str:
    """Return a normalised GUID or raise ValueError.

    Every id that came from the model is validated with this before it is placed
    into a URL or an OData filter, so a prompt-injected value cannot alter a query.
    """
    candidate = (value or "").strip().strip("{}").lower()
    if not _GUID_RE.match(candidate):
        raise ValueError(f"{name} must be a GUID, got {value!r}")
    return candidate


def odata_quote(value: str) -> str:
    """Quote a string literal for an OData $filter (single quotes are doubled)."""
    return "'" + value.replace("'", "''") + "'"
