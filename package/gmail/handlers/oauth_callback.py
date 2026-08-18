"""Exchange OAuth code and persist agent mailbox tokens (no Cognito)."""

from __future__ import annotations

import logging
from typing import Any, Dict
from urllib.parse import quote

from renglo.common import load_config, resolve_invite_fe_base_url
from renglo.data.data_controller import DataController

from .activity_log import ActivityLog
from .config import CONFIG_ORG, ConfigStore
from .oauth import (
    console_redirect_url,
    exchange_code,
    fetch_user_email,
    platform_oauth_client,
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

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        fe_base = ""
        try:
            fe_base = resolve_invite_fe_base_url(self.config) or ""
        except Exception:
            fe_base = (
                str(self.config.get("FE_BASE_URL") or self.config.get("INVITE_FE_BASE_URL") or "")
                .strip()
                .rstrip("/")
            )

        google_error = str(payload.get("error") or "").strip()
        if google_error:
            return {
                "success": False,
                "action": "oauth_callback",
                "redirect_url": f"{fe_base}/?gmail=error&error={quote(google_error)}",
                "message": google_error,
            }

        code = str(payload.get("code") or "").strip()
        state = str(payload.get("state") or "").strip()
        if not code or not state:
            return {
                "success": False,
                "action": "oauth_callback",
                "redirect_url": f"{fe_base}/?gmail=error&error=missing_code",
                "message": "code and state required",
            }

        try:
            claims = verify_state(state, self.config)
        except Exception as exc:
            _logger.warning("OAuth state verify failed: %s", exc)
            return {
                "success": False,
                "action": "oauth_callback",
                "redirect_url": f"{fe_base}/?gmail=error&error=invalid_state",
                "message": str(exc),
            }

        portfolio = str(claims["portfolio"])
        return_org = str(claims.get("return_org") or claims.get("org") or CONFIG_ORG)
        return_path = str(claims.get("return_path") or "")

        def _redirect(status: str, *, email: str = "", err: str = "") -> str:
            return console_redirect_url(
                fe_base=fe_base,
                portfolio=portfolio,
                org=return_org,
                return_path=return_path,
                status=status,
                email=email,
                error=err,
            )
        store = ConfigStore(self.DAC, portfolio, CONFIG_ORG)
        store.ensure_defaults()
        cfg = store.load_for_ingress()

        try:
            client_id, client_secret = platform_oauth_client(self.config, org_cfg=cfg)
            redirect_uri = redirect_uri_from_config(self.config)
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
