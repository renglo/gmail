"""Send a threaded Gmail reply using the org agent mailbox."""

from __future__ import annotations

import logging
from typing import Any, Dict

from renglo.common import load_config
from renglo.data.data_controller import DataController

from .config import CONFIG_ORG, ConfigStore
from .gmail_client import GmailClient, normalize_email
from .oauth import ensure_fresh_credentials, token_fields_from_credentials

_logger = logging.getLogger(__name__)


class ReplyMessage:
    def __init__(self) -> None:
        config = load_config()
        self.config = config
        self.DAC = DataController(config=config)

    def _client(self, portfolio: str):
        store = ConfigStore(self.DAC, portfolio, CONFIG_ORG)
        cfg = store.load_for_ingress()
        if not cfg.is_mailbox_connected():
            raise RuntimeError("Agent mailbox not connected")
        creds, changed = ensure_fresh_credentials(cfg, self.config)
        if changed:
            store.update_fields(token_fields_from_credentials(creds), system=True)
        return GmailClient(creds), cfg, store

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        portfolio = str(payload.get("portfolio") or "")
        if not portfolio:
            return {"success": False, "message": "portfolio required"}

        to = normalize_email(str(payload.get("to") or ""))
        text = str(payload.get("message") or payload.get("text") or payload.get("body") or "")
        thread_id = str(payload.get("thread_id") or payload.get("threadId") or "")
        subject = str(payload.get("subject") or "Re:")
        parent_message_id = str(
            payload.get("message_id_header")
            or payload.get("in_reply_to")
            or payload.get("parent_message_id_header")
            or ""
        )
        references = str(payload.get("references") or "")

        if not to or not text:
            return {"success": False, "message": "to and message required"}

        try:
            client, _cfg, _store = self._client(portfolio)
            result = client.reply_to_message(
                thread_id=thread_id,
                parent_message_id_header=parent_message_id,
                to=to,
                subject=subject,
                text=text,
                prior_references=references or None,
            )
        except Exception as exc:
            _logger.exception("Gmail reply failed")
            return {"success": False, "message": str(exc)}

        return {
            "success": True,
            "action": "reply_message",
            "output": result,
        }
