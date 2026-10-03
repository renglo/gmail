"""LINK token format helpers (shared semantics with WhatsApp / cos-demo)."""

from __future__ import annotations

import hashlib
import re
import secrets

CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
TOKEN_LEN = 20
LINK_CODE_TTL_SECONDS = 10 * 60

LINK_CODE_SEARCH_RE = re.compile(
    rf"(?<![A-Z0-9])LINK-([{CODE_ALPHABET}]{{{TOKEN_LEN}}}|[{CODE_ALPHABET}]{{4}})(?![A-Z0-9])",
    re.IGNORECASE,
)


def generate_link_token() -> str:
    body = "".join(secrets.choice(CODE_ALPHABET) for _ in range(TOKEN_LEN))
    return f"LINK-{body}"


def hash_link_code(code: str) -> str:
    normalized = code.strip().upper()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def extract_link_code(text: str) -> str | None:
    match = LINK_CODE_SEARCH_RE.search(text or "")
    if not match:
        return None
    return f"LINK-{match.group(1).upper()}"


def link_email_instructions(agent_email: str, code: str) -> str:
    return (
        f"Email {agent_email} with this code in the subject or body:\n\n"
        f"{code}\n\n"
        "The code expires in 10 minutes."
    )
