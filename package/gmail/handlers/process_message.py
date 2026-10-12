"""Dispatch a linked inbound email to the configured agent handler."""

from __future__ import annotations

import logging
from typing import Any, Dict

from renglo.auth.auth_controller import AuthController
from renglo.common import load_config
from renglo.data.data_controller import DataController
from renglo.schd.schd_loader import SchdLoader

from ..lib.describe import describe_document
from ..lib.session_coords import ensure_renglo_thread

_logger = logging.getLogger(__name__)


def extract_agent_text(agent_result: dict[str, Any]) -> str:
    if not isinstance(agent_result, dict):
        return ""
    outer = agent_result.get("output", agent_result)
    if isinstance(outer, dict) and "output" in outer and "success" in outer:
        handler_out = outer
    elif isinstance(outer, dict):
        handler_out = outer
    else:
        return str(outer) if outer else ""

    for key in ("message", "reply", "text", "response"):
        val = handler_out.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    nested = handler_out.get("output")
    if isinstance(nested, str) and nested.strip():
        return nested.strip()
    if isinstance(nested, dict):
        for key in ("message", "reply", "text", "response", "content"):
            val = nested.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    if isinstance(nested, list) and nested:
        last = nested[-1]
        if isinstance(last, str):
            return last.strip()
        if isinstance(last, dict):
            for key in ("message", "content", "text"):
                val = last.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    return ""


class ProcessMessage:
    def __init__(self) -> None:
        config = load_config()
        self.config = config
        self.DAC = DataController(config=config)
        self.AUC = AuthController(config=config)
        self.SHL = SchdLoader()

    def run_agent(
        self,
        *,
        agent_handler: str,
        portfolio: str,
        org: str,
        user_id: str,
        message: str,
        external_id: str,
        thread_id: str,
        subject: str = "",
        reply_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        parts = agent_handler.split("/")
        if len(parts) != 2:
            return {"success": False, "error": f"Invalid agent_handler: {agent_handler}"}

        extension, handler_name = parts[0], parts[1]
        class_name = self.SHL.convert_module_name_to_class(handler_name)
        instance = self.SHL.load_code_class(extension, handler_name, class_name)
        if not instance:
            return {"success": False, "error": f"Could not load {agent_handler}"}

        for attr in ("AUC", "DAC", "SHC", "CHC", "SSC"):
            controller = getattr(instance, attr, None)
            if controller is None:
                continue
            if hasattr(controller, "set_invocation_user"):
                controller.set_invocation_user(user_id)
            nested_auc = getattr(controller, "AUC", None)
            if nested_auc is not None and hasattr(nested_auc, "set_invocation_user"):
                nested_auc.set_invocation_user(user_id)

        self.AUC.set_invocation_user(user_id)
        self.DAC.AUC.set_invocation_user(user_id)

        gmail_thread_id = (thread_id or "").strip()
        if not gmail_thread_id:
            return {"success": False, "error": "gmail thread_id required"}

        coords = ensure_renglo_thread(
            config=self.config,
            portfolio=portfolio,
            org=org,
            user_id=user_id,
            gmail_thread_id=gmail_thread_id,
        )
        if not coords.get("success"):
            return {
                "success": False,
                "error": coords.get("message") or "Could not resolve Renglo thread",
                "coords": coords,
            }

        agent_message = message
        if subject:
            agent_message = f"Subject: {subject}\n\n{message}"

        outbound = dict(reply_args or {})
        outbound.setdefault("to", external_id)
        outbound.setdefault("thread_id", gmail_thread_id)
        if subject:
            outbound.setdefault("subject", subject)
        agent_payload = {
            "portfolio": portfolio,
            "org": org,
            "user_id": user_id,
            "public_user": user_id,
            "entity_type": coords["entity_type"],
            "entity_id": coords["entity_id"],
            "thread": coords["thread_id"],
            "channel": "gmail",
            "data": agent_message,
            "message": agent_message,
            "external_id": external_id,
            "subject": subject,
            "gmail_thread_id": gmail_thread_id,
            "reply_args": {key: value for key, value in outbound.items() if str(value or "").strip()},
        }
        try:
            result = instance.run(agent_payload)
            return {
                "success": True,
                "output": result,
                "entity_type": coords["entity_type"],
                "entity_id": coords["entity_id"],
                "thread_id": coords["thread_id"],
                "gmail_thread_id": gmail_thread_id,
            }
        except Exception as exc:
            _logger.exception("Agent dispatch failed")
            return {"success": False, "error": str(exc)}

    def describe(self, payload=None):
        return describe_document(
            "process_message",
            "Process inbound email",
            "Dispatch one linked inbound email to the agent and return the reply text. "
            "portfolio is injected by the platform. message also accepts data. thread_id also accepts thread.",
            {
                "org": {"type": "string", "title": "Org"},
                "user_id": {"type": "string", "title": "User id"},
                "message": {"type": "string", "title": "Message"},
                "external_id": {"type": "string", "title": "Sender email"},
                "thread_id": {"type": "string", "title": "Gmail thread id"},
                "subject": {"type": "string", "title": "Subject"},
                "agent_handler": {
                    "type": "string",
                    "title": "Agent handler",
                    "default": "dumbo/generic_agent",
                },
            },
            required=["org", "user_id", "message"],
            output_schema={
                "type": "object",
                "properties": {
                    "reply": {"type": "string"},
                    "agent": {"type": "object"},
                },
            },
        )

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        portfolio = str(payload.get("portfolio") or "")
        org = str(payload.get("org") or "")
        user_id = str(payload.get("user_id") or "")
        message = str(payload.get("message") or payload.get("data") or "")
        external_id = str(payload.get("external_id") or "")
        thread_id = str(payload.get("thread_id") or payload.get("thread") or "")
        subject = str(payload.get("subject") or "")
        agent_handler = str(payload.get("agent_handler") or "dumbo/generic_agent")
        raw_reply = payload.get("reply_args")
        reply_args = dict(raw_reply) if isinstance(raw_reply, dict) else {}
        for key in ("message_id_header", "references", "to"):
            if payload.get(key) and key not in reply_args:
                reply_args[key] = payload.get(key)

        if not all([portfolio, org, user_id, message]):
            return {"success": False, "message": "portfolio, org, user_id, message required"}

        agent_result = self.run_agent(
            agent_handler=agent_handler,
            portfolio=portfolio,
            org=org,
            user_id=user_id,
            message=message,
            external_id=external_id,
            thread_id=thread_id,
            subject=subject,
            reply_args=reply_args,
        )
        reply = extract_agent_text(agent_result)
        return {
            "success": bool(agent_result.get("success")),
            "action": "process_message",
            "reply": reply,
            "agent": agent_result,
            "output": {"reply": reply, "agent": agent_result},
        }
