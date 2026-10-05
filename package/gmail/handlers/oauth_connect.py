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
    normalize_console_return_url,
    portfolio_oauth_client,
    portfolio_state_secret,
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
            "portfolio is injected by the platform. Credentials and the state secret come from gmail_config.",
            {
                "org": {
                    "type": "string",
                    "title": "Return org",
                    "description": "Org the console was showing when Connect started.",
                },
                "return_url": {
                    "type": "string",
                    "title": "Return URL",
                    "description": "Absolute console URL of the browser that is already logged in.",
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
        if not portfolio:
            return {"success": False, "message": "portfolio required"}

        user_id = self.AUC.get_current_user()
        if not user_id:
            return {"success": False, "message": "Authentication required", "status": 401}

        store = ConfigStore(self.DAC, portfolio, CONFIG_ORG)
        store.ensure_defaults()
        cfg = store.load()

        try:
            return_url = normalize_console_return_url(str(payload.get("return_url") or ""))
            client_id, client_secret = portfolio_oauth_client(cfg)
            state_secret = portfolio_state_secret(cfg)
            redirect_uri = redirect_uri_from_config(self.config, cfg)
            code_verifier = generate_code_verifier()
            state = sign_state(
                portfolio=portfolio,
                state_secret=state_secret,
                org=CONFIG_ORG,
                return_org=return_org,
                return_url=return_url,
                code_verifier=code_verifier,
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
