"""Start OAuth consent for the portfolio agent mailbox (platform Google OAuth client)."""

from __future__ import annotations

from typing import Any, Dict

from renglo.auth.auth_controller import AuthController
from renglo.common import load_config
from renglo.data.data_controller import DataController

from ..lib.config import CONFIG_ORG, ConfigStore
from ..lib.describe import describe_document
from ..lib.oauth import (
    build_consent_url,
    generate_code_verifier,
    platform_oauth_client,
    redirect_uri_from_config,
    sign_state,
)


class OauthConnect:
    def __init__(self) -> None:
        config = load_config()
        self.config = config
        self.DAC = DataController(config=config)
        self.AUC = AuthController(config=config)

    def describe(self, payload=None):
        return describe_document(
            "oauth_connect",
            "Connect mailbox",
            "Start Google OAuth for the portfolio agent mailbox. Requires an authenticated user. "
            "portfolio is injected by the platform.",
            {
                "org": {
                    "type": "string",
                    "title": "Return org",
                    "description": "Org used when redirecting back to the console.",
                },
                "return_path": {
                    "type": "string",
                    "title": "Return path",
                    "description": "Console path to open after consent.",
                },
            },
            output_schema={
                "type": "object",
                "properties": {
                    "auth_url": {"type": "string"},
                    "redirect_uri": {"type": "string"},
                },
            },
        )

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        portfolio = str(payload.get("portfolio") or "")
        return_org = str(payload.get("org") or CONFIG_ORG)
        return_path = str(payload.get("return_path") or "").strip()
        if not portfolio:
            return {"success": False, "message": "portfolio required"}

        user_id = self.AUC.get_current_user()
        if not user_id:
            return {"success": False, "message": "Authentication required", "status": 401}

        store = ConfigStore(self.DAC, portfolio, CONFIG_ORG)
        store.ensure_defaults()
        cfg = store.load()

        try:
            client_id, client_secret = platform_oauth_client(self.config, org_cfg=cfg)
            redirect_uri = redirect_uri_from_config(self.config)
            code_verifier = generate_code_verifier()
            state = sign_state(
                portfolio=portfolio,
                org=CONFIG_ORG,
                return_org=return_org,
                return_path=return_path,
                code_verifier=code_verifier,
                config=self.config,
            )
            url = build_consent_url(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                state=state,
                code_verifier=code_verifier,
            )
        except Exception as exc:
            return {"success": False, "message": str(exc)}

        return {
            "success": True,
            "action": "oauth_connect",
            "auth_url": url,
            "redirect_uri": redirect_uri,
            "output": {"auth_url": url, "redirect_uri": redirect_uri},
        }
