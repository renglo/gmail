"""Google OAuth helpers for the agent mailbox (credentials live on each portfolio's gmail_config)."""

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
from urllib.parse import urlencode, urlparse

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


def portfolio_oauth_client(org_cfg: GmailConfig | None = None) -> tuple[str, str]:
    """OAuth Web client for this portfolio's ``gmail_config`` singleton."""
    client_id = (org_cfg.oauth_client_id if org_cfg else "").strip()
    client_secret = (org_cfg.oauth_client_secret if org_cfg else "").strip()
    if not client_id or not client_secret:
        raise ValueError(
            "gmail_config oauth_client_id and oauth_client_secret are required for this portfolio"
        )
    return client_id, client_secret


def portfolio_state_secret(org_cfg: GmailConfig | None = None) -> str:
    """HMAC key for OAuth state. Stored on the portfolio singleton, never a platform secret."""
    secret = (org_cfg.oauth_state_secret if org_cfg else "").strip()
    if not secret:
        raise ValueError("gmail_config.oauth_state_secret is required for this portfolio")
    return secret


def redirect_uri_from_config(
    config: dict[str, Any] | None = None,
    org_cfg: GmailConfig | None = None,
) -> str:
    """Callback URL sent to Google as redirect_uri.

    ``gmail_config.oauth_redirect_uri`` overwrites the default when set.
    Otherwise the callback is ``{BASE_URL}/_schd/gmail/oauth_callback``.
    The Google client's authorized redirect URI must match this value exactly.
    """
    override = (org_cfg.oauth_redirect_uri if org_cfg else "").strip()
    if override:
        parsed = urlparse(override)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("gmail_config.oauth_redirect_uri must be an absolute http(s) URL")
        return override.rstrip("/")
    cfg = config or {}
    base = (cfg.get("BASE_URL") or os.environ.get("BASE_URL") or "").strip().rstrip("/")
    if not base:
        raise ValueError("BASE_URL must be configured, or set gmail_config.oauth_redirect_uri")
    return f"{base}/_schd/gmail/oauth_callback"


def normalize_console_return_url(raw: str) -> str:
    """Absolute console URL for the browser that is already logged in.

    Query and fragment are dropped. The callback adds its own query on the way back.
    """
    text = str(raw or "").strip()
    if not text:
        raise ValueError(
            "return_url is required — send the console page this browser is already logged into"
        )
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("return_url must be an absolute http(s) console URL")
    if parsed.username or parsed.password:
        raise ValueError("return_url must not include credentials")
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or "/"
    return f"{parsed.scheme}://{parsed.hostname}{port}{path}"


def generate_code_verifier() -> str:
    """PKCE code_verifier (43–128 chars); must be reused on token exchange."""
    chars = ascii_letters + digits + "-._~"
    rnd = SystemRandom()
    return "".join(rnd.choice(chars) for _ in range(128))


def _decode_state_body(state: str) -> tuple[str, str, dict[str, Any]]:
    try:
        body, sig = state.rsplit(".", 1)
    except ValueError as exc:
        raise ValueError("Malformed OAuth state") from exc
    pad = "=" * (-len(body) % 4)
    try:
        payload = json.loads(urlsafe_b64decode(body + pad).decode("utf-8"))
    except Exception as exc:
        raise ValueError("Malformed OAuth state") from exc
    if not isinstance(payload, dict):
        raise ValueError("Malformed OAuth state")
    return body, sig, payload


def peek_state(state: str) -> dict[str, Any]:
    """Read the state body before signature check, so the portfolio secret can be loaded.

    The portfolio id is untrusted until ``verify_state`` succeeds with that portfolio's secret.
    """
    _body, _sig, payload = _decode_state_body(state)
    return payload


def sign_state(
    *,
    portfolio: str,
    state_secret: str,
    org: str = "_all",
    return_org: str = "",
    return_url: str = "",
    code_verifier: str = "",
    ttl_seconds: int = 600,
) -> str:
    """
    ``org`` is always the config ring org (``_all``).
    ``return_org`` is the console org embedded in ``return_url``.
    ``return_url`` is the absolute console page the logged-in browser should come back to.
    ``code_verifier`` is stored for PKCE token exchange on the callback.
    ``state_secret`` is ``gmail_config.oauth_state_secret`` for this portfolio.
    """
    secret = str(state_secret or "").strip()
    if not secret:
        raise ValueError("gmail_config.oauth_state_secret is required for this portfolio")
    payload = {
        "portfolio": portfolio,
        "org": org or "_all",
        "return_org": return_org or org or "_all",
        "return_url": normalize_console_return_url(return_url),
        "exp": int(time.time()) + ttl_seconds,
        "nonce": urlsafe_b64encode(os.urandom(12)).decode("ascii").rstrip("="),
    }
    verifier = str(code_verifier or "").strip()
    if verifier:
        payload["code_verifier"] = verifier
    body = urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
    sig = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_state(state: str, state_secret: str) -> dict[str, Any]:
    secret = str(state_secret or "").strip()
    if not secret:
        raise ValueError("gmail_config.oauth_state_secret is required for this portfolio")
    body, sig, payload = _decode_state_body(state)
    expected = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise ValueError("Invalid OAuth state signature")
    if int(payload.get("exp") or 0) < int(time.time()):
        raise ValueError("OAuth state expired")
    if not payload.get("portfolio") or not payload.get("org"):
        raise ValueError("OAuth state missing portfolio/org")
    if not payload.get("return_url"):
        raise ValueError("OAuth state missing return_url")
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
    del platform_config  # mailbox credentials live on gmail_config, not platform env
    client_id, client_secret = portfolio_oauth_client(cfg)
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
    return_url: str,
    status: str,
    email: str = "",
    error: str = "",
) -> str:
    """Land on the console URL captured while the browser was already logged in."""
    page = normalize_console_return_url(return_url)
    qs = urlencode({k: v for k, v in {"gmail": status, "as": email, "error": error}.items() if v})
    if not qs:
        return page
    joiner = "&" if "?" in page else "?"
    return f"{page}{joiner}{qs}"
