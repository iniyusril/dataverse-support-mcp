"""Configuration, read from environment variables (DV_*)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Mapping

from .util import require_guid

_TRUE = {"1", "true", "yes", "on"}

# Local parts (after stripping . _ -) that identify automated senders.
_AUTOMATED_PREFIXES = ("noreply", "donotreply", "mailerdaemon", "postmaster", "bounce")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    org_url: str
    tenant_id: str
    client_id: str
    client_secret: str
    api_version: str = "v9.2"
    support_address: str = ""
    sender_queue_id: str = ""
    sender_user_id: str = ""
    processed_field: str = "ts_hermes_processed"
    allow_send: bool = False
    reply_signature: str = ""
    blocked_senders: tuple[str, ...] = ()
    max_body_chars: int = 8000

    @property
    def api_base(self) -> str:
        return f"{self.org_url}/api/data/{self.api_version}"

    @property
    def scope(self) -> str:
        return f"{self.org_url}/.default"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env

        def get(name: str, default: str = "") -> str:
            return (env.get(name) or default).strip()

        required = ("DV_URL", "DV_TENANT_ID", "DV_CLIENT_ID", "DV_CLIENT_SECRET")
        missing = [name for name in required if not get(name)]
        if missing:
            raise ConfigError("Missing environment variables: " + ", ".join(missing))

        org_url = get("DV_URL").rstrip("/")
        if not org_url.startswith("https://"):
            raise ConfigError("DV_URL must start with https:// (e.g. https://yourorg.crm5.dynamics.com)")

        processed_field = get("DV_PROCESSED_FIELD", "ts_hermes_processed")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", processed_field):
            raise ConfigError("DV_PROCESSED_FIELD must be a lowercase Dataverse logical name")

        try:
            queue_id = require_guid(get("DV_SENDER_QUEUE_ID"), "DV_SENDER_QUEUE_ID") if get("DV_SENDER_QUEUE_ID") else ""
            user_id = require_guid(get("DV_SENDER_USER_ID"), "DV_SENDER_USER_ID") if get("DV_SENDER_USER_ID") else ""
            max_chars = int(get("DV_MAX_BODY_CHARS", "8000"))
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc

        blocked = tuple(
            item.strip().lower()
            for item in get("DV_BLOCKED_SENDERS").split(",")
            if item.strip()
        )

        return cls(
            org_url=org_url,
            tenant_id=get("DV_TENANT_ID"),
            client_id=get("DV_CLIENT_ID"),
            client_secret=get("DV_CLIENT_SECRET"),
            api_version=get("DV_API_VERSION", "v9.2"),
            support_address=get("DV_SUPPORT_ADDRESS").lower(),
            sender_queue_id=queue_id,
            sender_user_id=user_id,
            processed_field=processed_field,
            allow_send=get("DV_ALLOW_SEND").lower() in _TRUE,
            reply_signature=get("DV_REPLY_SIGNATURE").replace("\\n", "\n"),
            blocked_senders=blocked,
            max_body_chars=max(500, max_chars),
        )

    def blocked_reason(self, sender: str | None) -> str | None:
        """Return why we must not auto-reply to this sender, or None if it is fine."""
        address = parseaddr(sender or "")[1].strip().lower()
        if "@" not in address:
            return "sender address is missing or invalid"
        if self.support_address and address == self.support_address:
            return "sender is the support mailbox itself (reply loop)"
        local, _, domain = address.rpartition("@")
        normalised = re.sub(r"[._-]", "", local)
        if normalised.startswith(_AUTOMATED_PREFIXES):
            return "automated sender (no-reply / bounce address)"
        for rule in self.blocked_senders:
            if rule.startswith("@"):
                if domain == rule[1:]:
                    return f"sender domain is in DV_BLOCKED_SENDERS ({rule})"
            elif address == rule:
                return "sender is in DV_BLOCKED_SENDERS"
        return None
