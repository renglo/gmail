"""Google OAuth helpers for the agent mailbox (platform OAuth client, Asana/cos-demo style)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any
from random import SystemRandom
from string import ascii_letters, digits
from urllib.parse import urlencode

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from .config import GmailConfig

_logger = logging.getLogger(__name__)

GMAIL_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
REFRESH_LEEWAY_SEC = 60


def platform_oauth_client(
    config: dict[str, Any] | None = None,
    *,
    org_cfg: GmailConfig | None = None,
) -> tuple[str, str]:
    """
    Resolve OAuth Web client credentials.

    Primary: platform ``GOOGLE_OAUTH_CLIENT_ID`` / ``GOOGLE_OAUTH_CLIENT_SECRET``.
    Optional escape hatch: org ``gmail_config`` oauth_client_* if both set.
    """
    if org_cfg and org_cfg.oauth_client_id and org_cfg.oauth_client_secret:
        return org_cfg.oauth_client_id, org_cfg.oauth_client_secret
    cfg = config or {}
    client_id = (
        cfg.get("GOOGLE_OAUTH_CLIENT_ID") or os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or ""
    ).strip()
    client_secret = (
        cfg.get("GOOGLE_OAUTH_CLIENT_SECRET")
        or os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
        or ""
    ).strip()
    if not client_id or not client_secret:
        raise ValueError(
            "Platform GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET are not configured"
        )
    return client_id, client_secret


def is_platform_oauth_ready(config: dict[str, Any] | None = None) -> bool:
    try:
        platform_oauth_client(config)
        return True
    except ValueError:
        return False


def redirect_uri_from_config(config: dict[str, Any] | None = None) -> str:
    """Stable callback URL registered once on the platform OAuth client."""
    cfg = config or {}
    override = (cfg.get("GMAIL_OAUTH_REDIRECT_URI") or os.environ.get("GMAIL_OAUTH_REDIRECT_URI") or "").strip()
    if override:
        return override.rstrip("/")
    base = (cfg.get("BASE_URL") or os.environ.get("BASE_URL") or "").strip().rstrip("/")
    if not base:
        raise ValueError("BASE_URL or GMAIL_OAUTH_REDIRECT_URI must be configured")
    return f"{base}/_schd/gmail/oauth_callback"


def _state_secret(config: dict[str, Any] | None = None) -> str:
    cfg = config or {}
    secret = (
        cfg.get("OAUTH_STATE_SECRET")
        or os.environ.get("OAUTH_STATE_SECRET")
        or cfg.get("AUTH_SECRET")
        or os.environ.get("AUTH_SECRET")
        or cfg.get("SECRET_KEY")
        or os.environ.get("SECRET_KEY")
        or ""
    )
    if not secret:
        raise ValueError("OAUTH_STATE_SECRET / AUTH_SECRET must be configured for Gmail OAuth state")
    return str(secret)


def generate_code_verifier() -> str:
    """PKCE code_verifier (43–128 chars); must be reused on token exchange."""
    chars = ascii_letters + digits + "-._~"
    rnd = SystemRandom()
    return "".join(rnd.choice(chars) for _ in range(128))


def sign_state(
    *,
    portfolio: str,
    org: str = "_all",
    return_org: str = "",
    return_path: str = "",
    code_verifier: str = "",
    config: dict[str, Any] | None = None,
    ttl_seconds: int = 600,
) -> str:
    """
    ``org`` is always the config ring org (``_all``).
    ``return_org`` is the console org to redirect back to after Connect.
    ``return_path`` is the console path (e.g. ``/portfolio/org/gmail/settings``).
    ``code_verifier`` is stored for PKCE token exchange on the callback.
    """
    payload = {
        "portfolio": portfolio,
        "org": org or "_all",
        "return_org": return_org or org or "_all",
        "exp": int(time.time()) + ttl_seconds,
        "nonce": urlsafe_b64encode(os.urandom(12)).decode("ascii").rstrip("="),
    }
    path = str(return_path or "").strip()
    if path:
        payload["return_path"] = path if path.startswith("/") else f"/{path}"
    verifier = str(code_verifier or "").strip()
    if verifier:
        payload["code_verifier"] = verifier
    body = urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
    sig = hmac.new(_state_secret(config).encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_state(state: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        body, sig = state.rsplit(".", 1)
    except ValueError as exc:
        raise ValueError("Malformed OAuth state") from exc
    expected = hmac.new(_state_secret(config).encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise ValueError("Invalid OAuth state signature")
    pad = "=" * (-len(body) % 4)
    payload = json.loads(urlsafe_b64decode(body + pad).decode("utf-8"))
    if int(payload.get("exp") or 0) < int(time.time()):
        raise ValueError("OAuth state expired")
    if not payload.get("portfolio") or not payload.get("org"):
        raise ValueError("OAuth state missing portfolio/org")
    return payload


def _client_config(client_id: str, client_secret: str, redirect_uri: str) -> dict[str, Any]:
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": [redirect_uri],
        }
    }


def build_consent_url(
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    state: str,
    code_verifier: str,
) -> str:
    flow = Flow.from_client_config(
        _client_config(client_id, client_secret, redirect_uri),
        scopes=GMAIL_SCOPES,
        redirect_uri=redirect_uri,
        code_verifier=code_verifier,
        autogenerate_code_verifier=False,
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return auth_url


def exchange_code(
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
) -> Credentials:
    verifier = str(code_verifier or "").strip()
    if not verifier:
        raise ValueError("Missing PKCE code_verifier — start Connect again")
    flow = Flow.from_client_config(
        _client_config(client_id, client_secret, redirect_uri),
        scopes=GMAIL_SCOPES,
        redirect_uri=redirect_uri,
        code_verifier=verifier,
        autogenerate_code_verifier=False,
    )
    # Google may return a superset of scopes when the user previously granted
    # this OAuth client broader access (include_granted_scopes). oauthlib raises
    # otherwise; see OAUTHLIB_RELAX_TOKEN_SCOPE in oauthlib parameters.py.
    prev_relax = os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE")
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    try:
        flow.fetch_token(code=code)
    finally:
        if prev_relax is None:
            os.environ.pop("OAUTHLIB_RELAX_TOKEN_SCOPE", None)
        else:
            os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = prev_relax
    creds = flow.credentials
    if not creds or not creds.refresh_token:
        raise ValueError("No refresh_token returned — revoke app access and Connect again with prompt=consent")
    return creds


def credentials_from_config(
    cfg: GmailConfig,
    platform_config: dict[str, Any] | None = None,
) -> Credentials:
    client_id, client_secret = platform_oauth_client(platform_config, org_cfg=cfg)
    expiry = None
    if cfg.token_expiry:
        try:
            from datetime import datetime, timezone

            expiry = datetime.fromtimestamp(int(float(cfg.token_expiry)), tz=timezone.utc)
        except (TypeError, ValueError):
            expiry = None
    scopes = [s for s in (cfg.scopes or "").split() if s] or list(GMAIL_SCOPES)
    return Credentials(
        token=cfg.access_token or None,
        refresh_token=cfg.refresh_token or None,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
        expiry=expiry.replace(tzinfo=None) if expiry else None,
    )


def ensure_fresh_credentials(
    cfg: GmailConfig,
    platform_config: dict[str, Any] | None = None,
) -> tuple[Credentials, bool]:
    """Return credentials, refreshing if needed. Second value is True if tokens changed."""
    creds = credentials_from_config(cfg, platform_config)
    if not creds.refresh_token:
        raise ValueError("Mailbox not connected (missing refresh_token)")
    needs_refresh = not creds.valid or (
        creds.expired
        or (
            creds.expiry is not None
            and (creds.expiry.timestamp() - time.time()) < REFRESH_LEEWAY_SEC
        )
    )
    if needs_refresh:
        creds.refresh(Request())
        return creds, True
    return creds, False


def token_fields_from_credentials(creds: Credentials, email: str = "") -> dict[str, Any]:
    expiry = ""
    if creds.expiry:
        expiry = str(int(creds.expiry.timestamp()))
    fields: dict[str, Any] = {
        "access_token": creds.token or "",
        "refresh_token": creds.refresh_token or "",
        "token_expiry": expiry,
        "scopes": " ".join(creds.scopes or GMAIL_SCOPES),
    }
    if email:
        fields["email"] = email
    return fields


def fetch_user_email(creds: Credentials) -> str:
    import requests

    resp = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return str(data.get("email") or "").strip().lower()


def revoke_credentials(cfg: GmailConfig) -> None:
    token = cfg.refresh_token or cfg.access_token
    if not token:
        return
    try:
        import requests

        requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
    except Exception as exc:
        _logger.warning("Token revoke failed (best-effort): %s", exc)


def console_redirect_url(
    *,
    fe_base: str,
    portfolio: str,
    org: str,
    return_path: str = "",
    status: str,
    email: str = "",
    error: str = "",
    tool_handle: str = "gmail",
    section: str = "settings",
) -> str:
    base = (fe_base or "").rstrip("/")
    path = str(return_path or "").strip()
    if path and not path.startswith("/"):
        path = f"/{path}"
    if not path:
        path = f"/{portfolio}/{org}/{tool_handle}/{section}"
    qs = urlencode({k: v for k, v in {"gmail": status, "as": email, "error": error}.items() if v})
    if base:
        return f"{base}{path}?{qs}" if qs else f"{base}{path}"
    return f"{path}?{qs}" if qs else path
