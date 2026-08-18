"""Gmail API wrapper for list/get/reply/modify (agent mailbox)."""

from __future__ import annotations

import base64
import email.utils
import logging
import re
from email.message import EmailMessage
from typing import Any, Optional

from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

_logger = logging.getLogger(__name__)


def normalize_email(addr: str) -> str:
    _name, email_addr = email.utils.parseaddr(addr or "")
    return (email_addr or addr or "").strip().lower()


class GmailClient:
    def __init__(self, credentials: Credentials) -> None:
        self.service = build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def list_unread_inbox(self, *, max_results: int = 25) -> list[str]:
        resp = (
            self.service.users()
            .messages()
            .list(
                userId="me",
                q="in:inbox is:unread -in:drafts -in:sent",
                maxResults=max_results,
            )
            .execute()
        )
        return [m["id"] for m in resp.get("messages", [])]

    def list_messages_by_query(self, *, q: str, max_results: int = 10) -> list[str]:
        resp = (
            self.service.users()
            .messages()
            .list(userId="me", q=q, maxResults=max_results)
            .execute()
        )
        return [m["id"] for m in resp.get("messages", [])]

    def list_spam_link_candidates(self, *, max_results: int = 10) -> list[str]:
        """Spam that looks like a LINK attempt (includes read — user may have opened spam)."""
        return self.list_messages_by_query(
            q='in:spam "LINK-" newer_than:2d -in:drafts -in:sent',
            max_results=max_results,
        )

    def list_link_code_messages(self, code: str, *, max_results: int = 5) -> list[str]:
        """Find a specific LINK code in inbox or spam (read or unread)."""
        safe = (code or "").strip().replace('"', "")
        if not safe:
            return []
        return self.list_messages_by_query(
            q=f'"{safe}" (in:inbox OR in:spam) newer_than:2d -in:drafts -in:sent',
            max_results=max_results,
        )

    def list_unread_spam_link_candidates(self, *, max_results: int = 10) -> list[str]:
        """Backward-compatible alias for spam LINK scan."""
        return self.list_spam_link_candidates(max_results=max_results)

    def get_message(self, msg_id: str) -> dict[str, Any]:
        return (
            self.service.users()
            .messages()
            .modify(
                userId="me",
                id=msg_id,
                body={"removeLabelIds": ["SPAM"], "addLabelIds": ["INBOX"]},
            )
            .execute()
        )

    def get_message(self, msg_id: str) -> dict[str, Any]:
        msg = (
            self.service.users()
            .messages()
            .get(userId="me", id=msg_id, format="full")
            .execute()
        )
        payload = msg.get("payload") or {}
        headers = payload.get("headers") or []
        hdr: dict[str, str] = {}
        for h in headers:
            if isinstance(h, dict) and h.get("name") and h.get("value") is not None:
                hdr[str(h["name"]).lower()] = str(h["value"])

        text_body, html_body = self._get_payload_text(payload)
        body = (text_body or "").strip()
        if not body and html_body:
            body = self.extract_latest_reply_from_html(html_body)

        message_id_header = hdr.get("message-id", "")
        if message_id_header and not message_id_header.strip().startswith("<"):
            message_id_header = f"<{message_id_header.strip().strip('<>')}>"

        prior_references = hdr.get("references", "")
        if prior_references:
            refs = []
            for tok in prior_references.split():
                t = tok.strip().strip("<>")
                if t:
                    refs.append(f"<{t}>")
            prior_references = " ".join(refs)

        internal_ms = 0
        try:
            internal_ms = int(msg.get("internalDate") or 0)
        except (TypeError, ValueError):
            internal_ms = 0

        return {
            "msg_id": msg_id,
            "thread_id": msg.get("threadId") or "",
            "from": hdr.get("from", ""),
            "to": hdr.get("to", ""),
            "subject": hdr.get("subject", ""),
            "text": body,
            "html_body": html_body or "",
            "labels": msg.get("labelIds") or [],
            "message_id_header": message_id_header,
            "references": prior_references,
            "internal_ms": internal_ms,
            "snippet": msg.get("snippet") or "",
        }

    def mark_read(self, msg_id: str) -> dict[str, Any]:
        return (
            self.service.users()
            .messages()
            .modify(userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]})
            .execute()
        )

    def reply_to_message(
        self,
        *,
        thread_id: str,
        parent_message_id_header: str,
        to: str,
        subject: str,
        text: str,
        prior_references: Optional[str] = None,
    ) -> dict[str, Any]:
        if parent_message_id_header:
            parent_message_id_header = parent_message_id_header.strip().strip("<>")
            parent_message_id_header = f"<{parent_message_id_header}>"

        refs = ""
        if prior_references:
            cleaned = []
            for tok in str(prior_references).split():
                t = tok.strip().strip("<>")
                if t:
                    cleaned.append(f"<{t}>")
            refs = " ".join(cleaned)

        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        if parent_message_id_header:
            msg["In-Reply-To"] = parent_message_id_header
            msg["References"] = (refs + " " if refs else "") + parent_message_id_header
        msg.set_content(text or "")

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        body: dict[str, Any] = {"raw": raw}
        if thread_id:
            body["threadId"] = thread_id
        return self.service.users().messages().send(userId="me", body=body).execute()

    def _get_payload_text(self, payload: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        def decode_part(body: dict[str, Any]) -> Optional[str]:
            data = body.get("data")
            if not data:
                return None
            try:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            except Exception:
                return None

        if not isinstance(payload, dict):
            return None, None

        if payload.get("mimeType", "").startswith("multipart/"):
            text_body, html_body = None, None
            for part in payload.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                mime = part.get("mimeType", "")
                body = part.get("body") or {}
                if mime == "multipart/alternative" or mime.startswith("multipart/"):
                    nested_text, nested_html = self._get_payload_text(part)
                    text_body = text_body or nested_text
                    html_body = html_body or nested_html
                elif mime == "text/plain" and not text_body:
                    text_body = decode_part(body)
                elif mime == "text/html" and not html_body:
                    html_body = decode_part(body)
            return text_body, html_body

        mime = payload.get("mimeType", "")
        content = decode_part(payload.get("body") or {})
        if mime == "text/html":
            return None, content
        return content, None

    def extract_latest_reply_from_html(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for sel in [
            "div.gmail_quote",
            "div.gmail_quote_container",
            "blockquote.gmail_quote",
            "blockquote[type=cite]",
            "div.gmail_attr",
            "blockquote",
            "hr",
        ]:
            for node in soup.select(sel):
                node.decompose()
        text = soup.get_text("\n", strip=True)
        m = re.search(r"^\s*On .+ wrote:\s*$", text, flags=re.IGNORECASE | re.MULTILINE)
        if m:
            text = text[: m.start()]
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text
