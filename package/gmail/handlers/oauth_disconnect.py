"""Revoke and clear agent mailbox tokens."""

from __future__ import annotations

from typing import Any, Dict

from renglo.auth.auth_controller import AuthController
from renglo.common import load_config
from renglo.data.data_controller import DataController

from ..lib.activity_log import ActivityLog
from ..lib.config import CONFIG_ORG, ConfigStore
from ..lib.describe import describe_document
from ..lib.oauth import revoke_credentials


class OauthDisconnect:
    def __init__(self) -> None:
        config = load_config()
        self.DAC = DataController(config=config)
        self.AUC = AuthController(config=config)

    def describe(self, payload=None):
        return describe_document(
            "oauth_disconnect",
            "Disconnect mailbox",
            "Revoke and clear the agent mailbox tokens. Requires an authenticated user. "
            "portfolio is injected by the platform.",
            {},
            output_schema={
                "type": "object",
                "description": "Config update result after the tokens are cleared.",
            },
        )

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        portfolio = str(payload.get("portfolio") or "")
        if not portfolio:
            return {"success": False, "message": "portfolio required"}

        user_id = self.AUC.get_current_user()
        if not user_id:
            return {"success": False, "message": "Authentication required", "status": 401}

        store = ConfigStore(self.DAC, portfolio, CONFIG_ORG)
        cfg = store.load()
        previous_email = cfg.email
        revoke_credentials(cfg)
        updated = store.update_fields(
            {
                "email": "",
                "access_token": "",
                "refresh_token": "",
                "token_expiry": "",
                "scopes": "",
            }
        )
        if updated.get("success"):
            ActivityLog(self.DAC, portfolio).append(
                event_type="mailbox_disconnected",
                summary=f"Agent mailbox disconnected{f': {previous_email}' if previous_email else ''}",
                trigger="manual",
                actor_user_id=str(user_id),
                refs={"email": previous_email or ""},
            )
        return {
            "success": bool(updated.get("success")),
            "action": "oauth_disconnect",
            "output": updated,
        }
