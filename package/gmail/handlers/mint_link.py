"""Mint a LINK token for binding a personal email to the authenticated user."""

from __future__ import annotations

from typing import Any, Dict

from renglo.auth.auth_controller import AuthController
from renglo.common import load_config
from renglo.data.data_controller import DataController

from ..lib.config import CONFIG_ORG, ConfigStore
from ..lib.describe import describe_document
from ..lib.identity_store import IdentityStore
from ..lib.link_token import link_email_instructions


class MintLink:
    def __init__(self) -> None:
        config = load_config()
        self.DAC = DataController(config=config)
        self.AUC = AuthController(config=config)

    def describe(self, payload=None):
        return describe_document(
            "mint_link",
            "Mint link code",
            "Mint a LINK code that binds a personal email to the authenticated user. "
            "portfolio is injected by the platform.",
            {},
            output_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "expiresAt": {"type": "string", "format": "date-time"},
                    "agent_email": {"type": "string"},
                    "instructions": {"type": "string"},
                },
            },
        )

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        portfolio = str(payload.get("portfolio") or "")
        if not portfolio:
            return {"success": False, "message": "portfolio required"}

        user_id = self.AUC.get_current_user()
        if not user_id:
            return {"success": False, "message": "Authentication required", "status": 401}

        store = IdentityStore(self.DAC, portfolio, CONFIG_ORG)
        minted = store.mint_link_code(user_id)
        if not minted.get("success"):
            return minted

        cfg = ConfigStore(self.DAC, portfolio, CONFIG_ORG).load()
        agent_email = cfg.email or "the agent inbox"
        instructions = link_email_instructions(agent_email, minted["code"])

        return {
            "success": True,
            "action": "mint_link",
            "code": minted["code"],
            "expiresAt": minted["expires_at_iso"],
            "expires_at": minted["expires_at"],
            "agent_email": cfg.email,
            "instructions": instructions,
            "output": {
                "code": minted["code"],
                "expiresAt": minted["expires_at_iso"],
                "agent_email": cfg.email,
                "instructions": instructions,
            },
        }
