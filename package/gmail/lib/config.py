"""Load / ensure / update the portfolio-scoped singleton ``gmail_config`` (at ``_all``)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

_logger = logging.getLogger(__name__)

SINGLETON_ID = "00000000-0000-0000-0000-000000000000"
RING = "gmail_config"
CONFIG_ORG = "_all"


@dataclass
class GmailConfig:
    oauth_client_id: str = ""
    oauth_client_secret: str = ""
    oauth_state_secret: str = ""
    oauth_redirect_uri: str = ""
    email: str = ""
    access_token: str = ""
    refresh_token: str = ""
    token_expiry: str = ""
    scopes: str = ""
    agent_handler: str = "dumbo/generic_agent"
    enabled: bool = True
    last_internal_ms: str = "0"
    recent_message_ids: str = ""
    poll_batch_size: int = 25

    def poll_batch_limit(self) -> int:
        try:
            n = int(self.poll_batch_size)
        except (TypeError, ValueError):
            n = 25
        return max(1, min(n, 100))

    def is_mailbox_connected(self) -> bool:
        return bool(self.refresh_token and self.email)

    def is_poll_ready(self) -> bool:
        """Mailbox connected + enabled. Platform OAuth client is checked at token refresh."""
        return self.enabled and self.is_mailbox_connected()


class ConfigStore:
    """Reads / ensures / updates the extension singleton config (portfolio-scoped at ``_all``)."""

    def __init__(self, data_controller: Any, portfolio: str, org: str = CONFIG_ORG) -> None:
        self.DAC = data_controller
        self.portfolio = portfolio
        self.org = CONFIG_ORG  # always portfolio-wide; ignore call-site org

    def _parse_bool(self, raw: Any, default: bool = True) -> bool:
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        if raw is None:
            return default
        return bool(raw)

    def _parse_int(self, raw: Any, default: int, *, min_val: int = 1, max_val: int = 100) -> int:
        try:
            n = int(str(raw).strip())
        except (TypeError, ValueError):
            return default
        return max(min_val, min(n, max_val))

    def _from_doc(self, res: dict[str, Any]) -> GmailConfig:
        return GmailConfig(
            oauth_client_id=str(res.get("oauth_client_id") or "").strip(),
            oauth_client_secret=str(res.get("oauth_client_secret") or "").strip(),
            oauth_state_secret=str(res.get("oauth_state_secret") or "").strip(),
            oauth_redirect_uri=str(res.get("oauth_redirect_uri") or "").strip(),
            email=str(res.get("email") or "").strip(),
            access_token=str(res.get("access_token") or "").strip(),
            refresh_token=str(res.get("refresh_token") or "").strip(),
            token_expiry=str(res.get("token_expiry") or "").strip(),
            scopes=str(res.get("scopes") or "").strip(),
            agent_handler=str(res.get("agent_handler") or "dumbo/generic_agent").strip()
            or "dumbo/generic_agent",
            enabled=self._parse_bool(res.get("enabled"), True),
            last_internal_ms=str(res.get("last_internal_ms") or "0").strip() or "0",
            recent_message_ids=str(res.get("recent_message_ids") or "").strip(),
            poll_batch_size=self._parse_int(res.get("poll_batch_size"), 25),
        )

    def load_raw(self) -> dict[str, Any] | None:
        """Load singleton via DataModel (no authorize) — for poll / oauth callback."""
        try:
            row = self.DAC.DAM.get_a_b_c(self.portfolio, self.org, RING, SINGLETON_ID)
            if not row or row.get("error") or "_id" not in row:
                listed = self.DAC.DAM.get_a_b(self.portfolio, self.org, RING, limit=5)
                items = listed.get("items") or []
                if not items:
                    return None
                row = next(
                    (i for i in items if str(i.get("_id")) == SINGLETON_ID),
                    items[0],
                )
            attrs = dict(row.get("attributes") or {})
            if not attrs and isinstance(row, dict):
                attrs = {k: v for k, v in row.items() if not str(k).startswith("portfolio")}
            attrs["_id"] = row.get("_id") or attrs.get("_id") or SINGLETON_ID
            return attrs
        except Exception as exc:
            _logger.warning("Failed to load gmail_config (raw): %s", exc)
            return None

    def load(self) -> GmailConfig:
        try:
            res = self.DAC.get_a_b_c(self.portfolio, self.org, RING, SINGLETON_ID)
            if res.get("success") is False or "_id" not in res:
                raw = self.load_raw()
                if not raw:
                    _logger.warning("gmail_config not found; using defaults")
                    return GmailConfig()
                return self._from_doc(raw)
            return self._from_doc(res)
        except Exception as exc:
            _logger.warning("Failed to load gmail_config: %s", exc)
            raw = self.load_raw()
            if raw:
                return self._from_doc(raw)
            return GmailConfig()

    def load_for_ingress(self) -> GmailConfig:
        """Unauthenticated path used by poller and OAuth callback."""
        raw = self.load_raw()
        if not raw:
            return GmailConfig()
        return self._from_doc(raw)

    def ensure_defaults(self, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        defaults = {
            "_id": SINGLETON_ID,
            "oauth_client_id": "",
            "oauth_client_secret": "",
            "oauth_state_secret": "",
            "oauth_redirect_uri": "",
            "email": "",
            "access_token": "",
            "refresh_token": "",
            "token_expiry": "",
            "scopes": "",
            "agent_handler": "dumbo/generic_agent",
            "enabled": "true",
            "last_internal_ms": "0",
            "recent_message_ids": "",
            "poll_batch_size": "25",
        }
        if payload:
            defaults.update(payload)
        existing = self.DAC.get_a_b_c(self.portfolio, self.org, RING, SINGLETON_ID)
        if existing.get("success") is not False and "_id" in existing:
            return {
                "success": True,
                "action": "ensure_gmail_config",
                "message": "Config already present",
                "output": existing,
            }
        response, _status = self.DAC.post_a_b(self.portfolio, self.org, RING, defaults)
        return {
            "success": bool(response.get("success")),
            "action": "ensure_gmail_config",
            "message": "Config created" if response.get("success") else "Config create failed",
            "output": response,
        }

    def update_fields(self, fields: dict[str, Any], *, system: bool = False) -> dict[str, Any]:
        """Merge fields onto the singleton. system=True bypasses Cognito authorize."""
        clean = {k: v for k, v in fields.items() if v is not None}
        if not clean:
            return {"success": True, "message": "nothing to update"}
        if system:
            item = self.DAC.construct_put_item(
                self.portfolio, self.org, RING, SINGLETON_ID, clean
            )
            if "error" in item:
                return {"success": False, "output": item}
            result = self.DAC.DAM.put_a_b_c(
                self.portfolio, self.org, RING, SINGLETON_ID, item
            )
            return {"success": "error" not in result, "output": result}
        response, status = self.DAC.put_a_b_c(
            self.portfolio, self.org, RING, SINGLETON_ID, clean
        )
        return {
            "success": bool(response.get("success")),
            "status": status,
            "output": response,
        }
