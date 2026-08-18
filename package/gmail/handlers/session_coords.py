"""Renglo session coordinates for Gmail agent turns."""

from __future__ import annotations

from typing import Any

from renglo.session.session_controller import SessionController

# Per Gmail email-thread: do not overload a single user coordinate.
ENTITY_TYPE = "user-gmailthread"


def entity_id_for(user_id: str, gmail_thread_id: str) -> str:
    return f"{(user_id or '').strip()}-{(gmail_thread_id or '').strip()}"


def ensure_renglo_thread(
    *,
    config: dict[str, Any] | None,
    portfolio: str,
    org: str,
    user_id: str,
    gmail_thread_id: str,
) -> dict[str, Any]:
    """
    Resolve ``entity_type`` / ``entity_id`` / Renglo ``thread_id`` for an inbound
    Gmail message. Creates a Renglo thread document when none exist yet.
    """
    uid = (user_id or "").strip()
    gtid = (gmail_thread_id or "").strip()
    if not uid or not gtid:
        return {
            "success": False,
            "message": "user_id and gmail thread_id required for session coordinates",
        }

    eid = entity_id_for(uid, gtid)
    ssc = SessionController(config=config)
    ssc.set_invocation_user(uid)
    resolved = ssc.ensure_latest_thread(
        portfolio, org, ENTITY_TYPE, eid, public_user=uid
    )
    if not resolved.get("success"):
        return resolved

    doc = resolved.get("document") or {}
    thread_id = str(doc.get("_id") or "").strip()
    if not thread_id:
        return {
            "success": False,
            "message": "ensure_latest_thread returned no thread _id",
            "output": resolved,
        }

    return {
        "success": True,
        "entity_type": ENTITY_TYPE,
        "entity_id": eid,
        "thread_id": thread_id,
        "gmail_thread_id": gtid,
        "created": bool(resolved.get("created")),
        "document": doc,
    }


def agent_session_refs(agent_result: dict[str, Any] | None) -> dict[str, Any]:
    """Pull turn / entity coords from a channel agent dispatch result."""
    if not isinstance(agent_result, dict):
        return {}
    refs: dict[str, Any] = {
        "entity_type": agent_result.get("entity_type"),
        "entity_id": agent_result.get("entity_id"),
        "thread_id": agent_result.get("thread_id"),
    }
    outer = agent_result.get("output")
    summary: dict[str, Any] | None = None
    if isinstance(outer, dict):
        nested = outer.get("output")
        if isinstance(nested, dict) and (
            nested.get("turn_id") or nested.get("assistant_event_id")
        ):
            summary = nested
        elif outer.get("turn_id") or outer.get("assistant_event_id"):
            summary = outer
        elif isinstance(nested, dict):
            summary = nested
    if isinstance(summary, dict):
        refs["turn_id"] = summary.get("turn_id")
        refs["assistant_event_id"] = summary.get("assistant_event_id")
        refs["entity_type"] = refs.get("entity_type") or summary.get("entity_type")
        refs["entity_id"] = refs.get("entity_id") or summary.get("entity_id")
        refs["thread_id"] = (
            refs.get("thread_id")
            or summary.get("thread")
            or summary.get("thread_id")
        )
    return {k: v for k, v in refs.items() if v}


def record_channel_delivery(
    *,
    config: dict[str, Any] | None,
    portfolio: str,
    org: str,
    user_id: str,
    agent_result: dict[str, Any] | None,
    channel: str,
    external_id: str,
    send_result: dict[str, Any] | None,
    text: str = "",
) -> dict[str, Any]:
    """Persist send outcome on the same turn as the assistant reply."""
    refs = agent_session_refs(agent_result)
    turn_id = refs.get("turn_id")
    entity_type = refs.get("entity_type")
    entity_id = refs.get("entity_id")
    thread_id = refs.get("thread_id")
    if not all([turn_id, entity_type, entity_id, thread_id]):
        return {
            "success": False,
            "message": "missing session refs for channel_delivery",
            "refs": refs,
        }

    send = send_result if isinstance(send_result, dict) else {}
    ok = bool(send.get("success"))
    provider_error = send.get("error")
    provider_status = send.get("status")
    if provider_status is None and isinstance(provider_error, dict):
        err = provider_error.get("error")
        if isinstance(err, dict) and err.get("code") is not None:
            provider_status = err.get("code")

    ssc = SessionController(config=config)
    ssc.set_invocation_user(user_id)
    return ssc.append_channel_delivery(
        portfolio,
        org,
        entity_type,
        entity_id,
        thread_id,
        turn_id,
        channel=channel,
        status="sent" if ok else "failed",
        external_id=external_id,
        provider_status=provider_status,
        provider_error=provider_error,
        provider_message_id=send.get("id") or send.get("provider_message_id"),
        related_event_id=refs.get("assistant_event_id"),
        text_excerpt=(text or "").strip()[:240] or None,
    )
