"""Exchange OAuth code and persist agent mailbox tokens (no Cognito)."""

from __future__ import annotations

import logging
from typing import Any, Dict

from renglo.common import load_config
from renglo.data.data_controller import DataController

from ..lib.activity_log import ActivityLog
from ..lib.config import CONFIG_ORG, ConfigStore, GmailConfig
from ..lib.describe import describe_document
from ..lib.oauth import (
    console_redirect_url,
    exchange_code,
    fetch_user_email,
    peek_state,
    portfolio_oauth_client,
    redirect_uri_from_config,
    token_fields_from_credentials,
    verify_state,
)

_logger = logging.getLogger(__name__)


class OauthCallback:
    def __init__(self) -> None:
        config = load_config()
        self.config = config
        self.DAC = DataController(config=config)

    def describe(self, payload=None):
        return describe_document(
            "oauth_callback",
            "OAuth callback",
            "Exchange the Google authorization code and store the agent mailbox tokens. "
            "Called by the OAuth redirect. Send code and state together, or error when consent is denied.",
            {
                "code": {"type": "string", "title": "Authorization code"},
                "state": {"type": "string", "title": "Signed state"},
                "error": {
                    "type": "string",
                    "title": "Provider error",
                    "description": "Set by Google when the user denies consent.",
                },
            },
            output_schema={
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "redirect_url": {"type": "string"},
                },
            },
        )

    def _verified(self, state: str) -> tuple[dict[str, Any], GmailConfig, ConfigStore]:
        peeked = peek_state(state)
        portfolio = str(peeked.get("portfolio") or "").strip()
        if not portfolio:
            raise ValueError("OAuth state missing portfolio")
        store = ConfigStore(self.DAC, portfolio, CONFIG_ORG)
        cfg = store.load_for_ingress()
        claims = verify_state(state, cfg.oauth_state_secret)
        if str(claims.get("portfolio") or "") != portfolio:
            raise ValueError("OAuth state portfolio mismatch")
        return claims, cfg, store

    def _fail(self, message: str, *, redirect_url: str = "") -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "success": False,
            "action": "oauth_callback",
            "message": message,
        }
        if redirect_url:
            out["redirect_url"] = redirect_url
        return out

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        google_error = str(payload.get("error") or "").strip()
        code = str(payload.get("code") or "").strip()
        state = str(payload.get("state") or "").strip()

        claims: dict[str, Any] | None = None
        cfg: GmailConfig | None = None
        store: ConfigStore | None = None
        if state:
            try:
                claims, cfg, store = self._verified(state)
            except Exception as exc:
                _logger.warning("OAuth state verify failed: %s", exc)
                return self._fail(str(exc))

        def _redirect(status: str, *, email: str = "", err: str = "") -> str:
            return console_redirect_url(
                return_url=str((claims or {}).get("return_url") or ""),
                status=status,
                email=email,
                error=err,
            )

        if google_error:
            redirect_url = ""
            if claims:
                redirect_url = _redirect("error", err=google_error[:180])
            return self._fail(google_error, redirect_url=redirect_url)

        if not code or not claims or cfg is None or store is None:
            redirect_url = ""
            if claims:
                redirect_url = _redirect("error", err="missing_code")
            return self._fail("code and state required", redirect_url=redirect_url)

        portfolio = str(claims["portfolio"])

        try:
            client_id, client_secret = portfolio_oauth_client(cfg)
            redirect_uri = redirect_uri_from_config(self.config, cfg)
            creds = exchange_code(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                code=code,
                code_verifier=str(claims.get("code_verifier") or ""),
            )
            email = fetch_user_email(creds)
            fields = token_fields_from_credentials(creds, email=email)
            if not fields.get("refresh_token") and cfg.refresh_token:
                fields["refresh_token"] = cfg.refresh_token
            updated = store.update_fields(fields, system=True)
            if not updated.get("success"):
                raise RuntimeError(f"Failed to persist tokens: {updated}")
        except Exception as exc:
            _logger.exception("OAuth callback exchange failed")
            return {
                "success": False,
                "action": "oauth_callback",
                "redirect_url": _redirect("error", err=str(exc)[:180]),
                "message": str(exc),
            }

        ActivityLog(self.DAC, portfolio).append(
            event_type="mailbox_connected",
            summary=f"Agent mailbox connected: {email}",
            trigger="oauth",
            refs={"email": email, "portfolio": portfolio},
        )

        return {
            "success": True,
            "action": "oauth_callback",
            "email": email,
            "portfolio": portfolio,
            "org": CONFIG_ORG,
            "redirect_url": _redirect("linked", email=email),
            "output": {"email": email},
        }
