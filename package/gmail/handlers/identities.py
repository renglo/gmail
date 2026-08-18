"""List / unlink Gmail identities for the authenticated user."""

from __future__ import annotations

from typing import Any, Dict

from renglo.auth.auth_controller import AuthController
from renglo.common import load_config
from renglo.data.data_controller import DataController

from .config import CONFIG_ORG
from .identity_store import IdentityStore


class Identities:
    def __init__(self) -> None:
        config = load_config()
        self.DAC = DataController(config=config)
        self.AUC = AuthController(config=config)

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        portfolio = str(payload.get("portfolio") or "")
        if not portfolio:
            return {"success": False, "message": "portfolio required"}

        user_id = self.AUC.get_current_user()
        if not user_id:
            return {"success": False, "message": "Authentication required", "status": 401}

        action = str(payload.get("action") or payload.get("subhandler") or "list").lower()
        store = IdentityStore(self.DAC, portfolio, CONFIG_ORG)

        if action in ("list", "status", ""):
            items = store.list_for_user(user_id, authenticated=True)
            return {
                "success": True,
                "action": "list",
                "user_id": user_id,
                "items": items,
                "output": items,
            }

        if action in ("unlink", "delete"):
            external_id = payload.get("external_id")
            result = store.unlink(user_id, external_id=str(external_id) if external_id else None)
            return {
                "success": True,
                "action": "unlink",
                "output": result,
            }

        return {"success": False, "message": f"Unknown action: {action}"}
