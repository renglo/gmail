"""Poll the agent inbox: LINK gate + agent dispatch + threaded reply."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from renglo.common import load_config
from renglo.data.data_controller import DataController

from ..lib.activity_log import ActivityLog
from ..lib.config import CONFIG_ORG, ConfigStore
from ..lib.describe import describe_document
from ..lib.gmail_client import GmailClient, normalize_email
from ..lib.identity_store import IdentityStore, extract_code_from_text
from ..lib.oauth import ensure_fresh_credentials, token_fields_from_credentials
from .process_message import ProcessMessage, extract_agent_text
from ..lib.session_coords import record_channel_delivery

_logger = logging.getLogger(__name__)

LINKED_REPLY = "✓ Connected. You can email me here anytime."
RECENT_IDS_CAP = 40
# Spam is only scanned while a LINK code is pending (see run()). Caps limit flood abuse.
SPAM_FETCH_CAP = 10
SPAM_PROCESS_CAP = 5
SPAM_INVALID_STOP = 3


class PollInbox:
    def __init__(self) -> None:
        config = load_config()
        self.config = config
        self.DAC = DataController(config=config)
        self.processor = ProcessMessage()

    def _parse_recent_ids(self, raw: str) -> list[str]:
        return [p.strip() for p in (raw or "").split(",") if p.strip()]

    def _client_and_store(self, portfolio: str):
        store = ConfigStore(self.DAC, portfolio, CONFIG_ORG)
        cfg = store.load_for_ingress()
        if not cfg.is_poll_ready():
            return None, cfg, store
        creds, changed = ensure_fresh_credentials(cfg, self.config)
        if changed:
            store.update_fields(token_fields_from_credentials(creds), system=True)
            cfg = store.load_for_ingress()
        return GmailClient(creds), cfg, store

    def _reply(
        self,
        client: GmailClient,
        msg: dict[str, Any],
        text: str,
    ) -> dict[str, Any]:
        try:
            data = client.reply_to_message(
                thread_id=msg.get("thread_id") or "",
                parent_message_id_header=msg.get("message_id_header") or "",
                to=normalize_email(msg.get("from") or ""),
                subject=msg.get("subject") or "Re:",
                text=text,
                prior_references=msg.get("references") or None,
            )
            mid = data.get("id") if isinstance(data, dict) else None
            return {"success": True, "id": mid, "output": data}
        except Exception as exc:
            _logger.exception("Gmail reply failed")
            return {"success": False, "error": str(exc)}

    def _handle_message(
        self,
        *,
        portfolio: str,
        org: str,
        cfg,
        store_ids: IdentityStore,
        client: GmailClient,
        msg: dict[str, Any],
        from_spam: bool = False,
    ) -> dict[str, Any]:
        external_id = normalize_email(msg.get("from") or "")
        agent_email = normalize_email(cfg.email)
        if not external_id:
            return {"success": True, "action": "skip", "reason": "no_from"}
        if agent_email and external_id == agent_email:
            return {"success": True, "action": "skip", "reason": "self"}

        labels = set(msg.get("labels") or [])
        if "DRAFT" in labels or "SENT" in labels:
            return {"success": True, "action": "skip", "reason": "draft_or_sent"}

        text = (msg.get("text") or "").strip() or (msg.get("snippet") or "").strip()
        subject = msg.get("subject") or ""
        haystack = f"{subject}\n{text}"
        link_code = extract_code_from_text(haystack)
        identity = store_ids.resolve_identity(external_id)

        if from_spam:
            if not link_code:
                return {"success": True, "action": "spam_skip", "reason": "no_link_pattern"}
            if not store_ids.is_valid_pending_code(link_code):
                client.mark_read(msg["msg_id"])
                return {
                    "success": True,
                    "action": "spam_link_invalid",
                    "reason": "no_matching_pending_code",
                    "external_id": external_id,
                }

        if identity:
            store_ids.touch_last_seen(identity)
            user_id = str(identity.get("user_id") or "")
            if link_code:
                self._reply(client, msg, "✓ Already connected.")
                client.mark_read(msg["msg_id"])
                return {
                    "success": True,
                    "action": "already_linked",
                    "user_id": user_id,
                    "external_id": external_id,
                }

            if from_spam:
                client.mark_read(msg["msg_id"])
                return {
                    "success": True,
                    "action": "spam_skip",
                    "reason": "already_linked",
                    "external_id": external_id,
                }

            agent_result = self.processor.run_agent(
                agent_handler=cfg.agent_handler,
                portfolio=portfolio,
                org=CONFIG_ORG,
                user_id=user_id,
                message=text,
                external_id=external_id,
                thread_id=msg.get("thread_id") or "",
                subject=subject,
            )
            reply = extract_agent_text(agent_result)
            send_result = None
            delivery = None
            if reply:
                send_result = self._reply(client, msg, reply)
                delivery = record_channel_delivery(
                    config=self.config,
                    portfolio=portfolio,
                    org=CONFIG_ORG,
                    user_id=user_id,
                    agent_result=agent_result,
                    channel="gmail",
                    external_id=external_id,
                    send_result=send_result,
                    text=reply,
                )
                if not (delivery or {}).get("success"):
                    _logger.warning(
                        "Could not persist channel_delivery: %s", delivery
                    )
                if not (send_result or {}).get("success"):
                    _logger.error(
                        "Gmail delivery failed for %s: %s",
                        external_id,
                        send_result,
                    )
            elif not agent_result.get("success"):
                _logger.error("Agent failed: %s", agent_result)
            client.mark_read(msg["msg_id"])
            return {
                "success": True,
                "action": "agent",
                "user_id": user_id,
                "external_id": external_id,
                "agent": agent_result,
                "reply": reply,
                "send": send_result,
                "delivery": delivery,
            }

        if link_code:
            result = store_ids.consume_link_code(
                external_id=external_id,
                code=link_code,
                display_name=external_id,
            )
            status = result.get("status")
            if status == "linked":
                if from_spam:
                    try:
                        client.move_from_spam_to_inbox(msg["msg_id"])
                    except Exception as exc:
                        _logger.warning("move_from_spam_to_inbox failed: %s", exc)
                self._reply(client, msg, LINKED_REPLY)
            client.mark_read(msg["msg_id"])
            return {
                "success": True,
                "action": "link_consume",
                "status": status,
                "external_id": external_id,
                "from_spam": from_spam,
                "result": result,
                "reply_sent": status == "linked",
            }

        if from_spam:
            return {"success": True, "action": "spam_skip", "reason": "no_link_code"}

        client.mark_read(msg["msg_id"])
        return {
            "success": True,
            "action": "unlinked_skip",
            "reason": "not_linked",
            "external_id": external_id,
        }

    def _collect_spam_link_messages(
        self,
        client: GmailClient,
        store_ids: IdentityStore,
        *,
        link_code_hint: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        """Fetch spam LINK candidates; prioritize messages matching pending codes."""
        if not store_ids.has_pending_link_codes():
            return [], 0

        hint = (link_code_hint or "").strip()
        fetch_errors = 0
        try:
            if hint and store_ids.is_valid_pending_code(hint):
                spam_ids = client.list_link_code_messages(hint, max_results=SPAM_PROCESS_CAP)
            else:
                spam_ids = client.list_spam_link_candidates(max_results=SPAM_FETCH_CAP)
        except Exception as exc:
            _logger.warning("spam/link-code list failed: %s", exc)
            return [], 0

        candidates: list[tuple[bool, int, dict[str, Any]]] = []
        for msg_id in spam_ids:
            try:
                msg = client.get_message(msg_id)
            except Exception as exc:
                _logger.warning("get_message (spam) %s failed: %s", msg_id, exc)
                fetch_errors += 1
                continue
            haystack = f"{msg.get('subject') or ''}\n{(msg.get('text') or '').strip() or (msg.get('snippet') or '')}"
            code = extract_code_from_text(haystack)
            pending_match = bool(code and store_ids.is_valid_pending_code(code))
            internal_ms = int(msg.get("internal_ms") or 0)
            candidates.append((pending_match, internal_ms, msg))

        # Valid pending codes first, then oldest among ties.
        candidates.sort(key=lambda row: (not row[0], row[1]))
        return [row[2] for row in candidates[:SPAM_PROCESS_CAP]], fetch_errors

    def describe(self, payload=None):
        return describe_document(
            "poll_inbox",
            "Poll inbox",
            "Poll the agent mailbox for unread mail and pending LINK codes, then dispatch linked messages. "
            "portfolio is injected by the platform.",
            {
                "trigger": {
                    "type": "string",
                    "title": "Trigger",
                    "description": "Who started the poll, recorded on the activity log.",
                    "default": "cron",
                },
                "link_code": {
                    "type": "string",
                    "title": "Link code hint",
                    "description": "Optional LINK code to prefer while scanning spam.",
                },
            },
            output_schema={
                "type": "array",
                "description": "One result per message handled in this poll.",
                "items": {"type": "object"},
            },
        )

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        portfolio = str(payload.get("portfolio") or "")
        if not portfolio:
            return {"success": False, "message": "portfolio required"}

        trigger = str(payload.get("trigger") or "cron").strip() or "cron"
        activity = ActivityLog(self.DAC, portfolio)

        client, cfg, store = self._client_and_store(portfolio)
        if client is None:
            activity.append(
                event_type="poll_skipped",
                summary="Poll skipped — mailbox not connected or disabled",
                trigger=trigger,
                refs={"enabled": cfg.enabled, "connected": cfg.is_mailbox_connected()},
            )
            return {
                "success": True,
                "action": "skip",
                "message": "poll not ready (disabled or mailbox not connected)",
            }

        batch_size = cfg.poll_batch_limit()
        try:
            last_ms = int(float(cfg.last_internal_ms or "0"))
        except (TypeError, ValueError):
            last_ms = 0
        recent = set(self._parse_recent_ids(cfg.recent_message_ids))

        store_ids = IdentityStore(self.DAC, portfolio, CONFIG_ORG)
        pending_link_codes = store_ids.has_pending_link_codes()
        link_code_hint = str(payload.get("link_code") or "").strip()

        try:
            msg_ids = client.list_unread_inbox(max_results=batch_size)
        except Exception as exc:
            _logger.exception("list_unread_inbox failed")
            activity.append(
                event_type="poll_error",
                summary=f"Poll failed listing inbox: {exc}",
                trigger=trigger,
                refs={"poll_batch_size": batch_size},
            )
            return {"success": False, "message": str(exc)}

        spam_messages, spam_fetch_errors = self._collect_spam_link_messages(
            client, store_ids, link_code_hint=link_code_hint
        )
        inbox_id_set = set(msg_ids)

        results: List[Dict[str, Any]] = []
        message_refs: list[dict[str, Any]] = []
        skipped_recent = 0
        fetch_errors = spam_fetch_errors
        spam_invalid_streak = 0
        max_internal = last_ms
        processed_order: list[str] = []

        for msg_id in reversed(msg_ids):  # oldest first among batch
            if msg_id in recent:
                try:
                    peek = client.get_message(msg_id)
                except Exception as exc:
                    _logger.warning("get_message (recent peek) %s failed: %s", msg_id, exc)
                    fetch_errors += 1
                    message_refs.append(
                        {"msg_id": msg_id, "action": "fetch_error", "error": str(exc)[:200]}
                    )
                    continue
                # Still unread → allow retry (e.g. unlinked_skip before user linked, or user
                # marked unread again). Read messages in recent are true dedup hits.
                if "UNREAD" not in set(peek.get("labels") or []):
                    skipped_recent += 1
                    message_refs.append(
                        ActivityLog.message_ref(
                            peek,
                            {
                                "action": "skipped_recent",
                                "skip_reason": "recent_message_ids",
                            },
                        )
                    )
                    continue
                msg = peek
            else:
                try:
                    msg = client.get_message(msg_id)
                except Exception as exc:
                    _logger.warning("get_message %s failed: %s", msg_id, exc)
                    fetch_errors += 1
                    message_refs.append(
                        {"msg_id": msg_id, "action": "fetch_error", "error": str(exc)[:200]}
                    )
                    continue

            internal_ms = int(msg.get("internal_ms") or 0)

            handled = self._handle_message(
                portfolio=portfolio,
                org=CONFIG_ORG,
                cfg=cfg,
                store_ids=store_ids,
                client=client,
                msg=msg,
            )
            results.append(handled)
            message_refs.append(ActivityLog.message_ref(msg, handled))
            processed_order.append(msg_id)
            if internal_ms > max_internal:
                max_internal = internal_ms

        for msg in spam_messages:
            msg_id = str(msg.get("msg_id") or "")
            if not msg_id or msg_id in processed_order:
                continue
            labels = set(msg.get("labels") or [])
            from_spam = "SPAM" in labels
            if msg_id in recent and "UNREAD" not in labels:
                continue
            if msg_id in inbox_id_set and not from_spam:
                continue
            if spam_invalid_streak >= SPAM_INVALID_STOP:
                message_refs.append(
                    ActivityLog.message_ref(
                        msg,
                        {"action": "spam_skipped", "reason": "invalid_streak_cap"},
                    )
                )
                continue

            handled = self._handle_message(
                portfolio=portfolio,
                org=CONFIG_ORG,
                cfg=cfg,
                store_ids=store_ids,
                client=client,
                msg=msg,
                from_spam=from_spam,
            )
            results.append(handled)
            message_refs.append(ActivityLog.message_ref(msg, handled))
            processed_order.append(msg_id)
            internal_ms = int(msg.get("internal_ms") or 0)
            if internal_ms > max_internal:
                max_internal = internal_ms

            if handled.get("action") == "spam_link_invalid":
                spam_invalid_streak += 1
            elif handled.get("action") == "link_consume":
                spam_invalid_streak = 0

        if processed_order or max_internal > last_ms:
            merged = list(dict.fromkeys(processed_order + list(recent)))[:RECENT_IDS_CAP]
            store.update_fields(
                {
                    "last_internal_ms": str(max_internal),
                    "recent_message_ids": ",".join(merged),
                },
                system=True,
            )

        action_counts: dict[str, int] = {}
        for ref in message_refs:
            key = str(ref.get("action") or "unknown")
            action_counts[key] = action_counts.get(key, 0) + 1

        spam_polled = pending_link_codes
        activity.append(
            event_type="poll",
            summary=(
                f"Poll ({trigger}): fetched {len(msg_ids)} inbox + "
                f"{len(spam_messages)} spam LINK, processed {len(results)}, "
                f"skipped {skipped_recent} recent"
            ),
            trigger=trigger,
            refs={
                "poll_batch_size": batch_size,
                "fetched_count": len(msg_ids),
                "spam_link_fetched_count": len(spam_messages),
                "spam_poll_enabled": spam_polled,
                "processed_count": len(results),
                "skipped_recent_count": skipped_recent,
                "fetch_error_count": fetch_errors,
                "spam_invalid_streak_cap": SPAM_INVALID_STOP,
                "action_counts": action_counts,
                "agent_email": cfg.email,
            },
            detail={
                "trigger": trigger,
                "poll_batch_size": batch_size,
                "fetched_msg_ids": msg_ids,
                "spam_msg_ids": [m.get("msg_id") for m in spam_messages],
                "messages": message_refs,
                "handler_results": results,
            },
        )

        return {
            "success": True,
            "action": "poll_inbox",
            "count": len(results),
            "trigger": trigger,
            "fetched": len(msg_ids),
            "spam_link_fetched": len(spam_messages),
            "poll_batch_size": batch_size,
            "output": results,
        }
