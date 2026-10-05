"""Portfolio OAuth state is signed with gmail_config.oauth_state_secret."""

import sys
import types

for _name in (
    "google",
    "google.auth",
    "google.auth.transport",
    "google.auth.transport.requests",
    "google.oauth2",
    "google.oauth2.credentials",
    "google_auth_oauthlib",
    "google_auth_oauthlib.flow",
):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["google.auth.transport.requests"].Request = object
sys.modules["google.oauth2.credentials"].Credentials = object
sys.modules["google_auth_oauthlib.flow"].Flow = object

from gmail.lib.config import GmailConfig
from gmail.lib.oauth import (
    console_redirect_url,
    normalize_console_return_url,
    peek_state,
    redirect_uri_from_config,
    sign_state,
    verify_state,
)


def test_round_trip_uses_portfolio_secret_and_return_url():
    secret = "portfolio-state-secret"
    page = "https://staging.example.amplifyapp.com/port/_all/tool/settings"
    state = sign_state(
        portfolio="port",
        state_secret=secret,
        return_org="acme",
        return_url=page + "?stale=1",
        code_verifier="verifier-value",
    )
    peeked = peek_state(state)
    assert peeked["portfolio"] == "port"
    claims = verify_state(state, secret)
    assert claims["return_url"] == page
    assert claims["return_org"] == "acme"
    assert claims["code_verifier"] == "verifier-value"
    landed = console_redirect_url(return_url=claims["return_url"], status="linked", email="agent@acme.com")
    assert landed.startswith(page + "?")
    assert "gmail=linked" in landed
    assert "as=agent%40acme.com" in landed


def test_other_portfolio_secret_does_not_verify():
    state = sign_state(
        portfolio="port",
        state_secret="secret-a",
        return_url="https://console.example/port/org/gmail/settings",
    )
    try:
        verify_state(state, "secret-b")
    except ValueError as exc:
        assert "signature" in str(exc)
    else:
        raise AssertionError("expected signature failure")


def test_redirect_uri_comes_from_gmail_config_or_base_url():
    override = GmailConfig(oauth_redirect_uri="https://api.example.com/custom/callback/")
    assert (
        redirect_uri_from_config({"BASE_URL": "https://ignored.example", "GMAIL_OAUTH_REDIRECT_URI": "https://nope.example/cb"}, override)
        == "https://api.example.com/custom/callback"
    )
    assert (
        redirect_uri_from_config({"BASE_URL": "https://api.example.com/production/"}, GmailConfig())
        == "https://api.example.com/production/_schd/gmail/oauth_callback"
    )


def test_missing_secret_and_bad_return_url_are_rejected():
    try:
        sign_state(portfolio="port", state_secret="", return_url="https://console.example/settings")
    except ValueError as exc:
        assert "oauth_state_secret" in str(exc)
    else:
        raise AssertionError("expected missing secret")

    try:
        normalize_console_return_url("javascript:alert(1)")
    except ValueError as exc:
        assert "absolute" in str(exc)
    else:
        raise AssertionError("expected bad return url")
