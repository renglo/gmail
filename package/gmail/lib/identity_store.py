"""channel_identities + channel_link_codes for Gmail (org-scoped)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from .activity_log import ActivityLog
from .config import CONFIG_ORG
from .link_token import (
    LINK_CODE_TTL_SECONDS,
    extract_link_code,
    generate_link_token,
    hash_link_code,
)

_logger = logging.getLogger(__name__)

CHANNEL = "gmail"
IDENTITIES_RING = "channel_identities"
CODES_RING = "channel_link_codes"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _attrs(row: dict[str, Any]) -> dict[str, Any]:
    attrs = dict(row.get("attributes") or {})
    if "_id" in row:
        attrs["_id"] = row["_id"]
    elif "_id" not in attrs and row.get("_id"):
        attrs["_id"] = row["_id"]
    if not attrs and row:
        attrs = dict(row)
    return attrs


class IdentityStore:
    def __init__(self, data_controller: Any, portfolio: str, org: str = CONFIG_ORG) -> None:
        self.DAC = data_controller
        self.portfolio = portfolio
        self.org = CONFIG_ORG  # portfolio-wide, same as gmail_config

    def _query(self, ring: str, value: str) -> list[dict[str, Any]]:
        response = self.DAC.get_a_b_query(
            {
                "portfolio": self.portfolio,
                "org": self.org,
                "ring": ring,
                "operator": "begins_with",
                "value": value,
                "limit": 50,
                "lastkey": None,
                "sort": "desc",
            }
        )
        if not response.get("success"):
            return []
        return list(response.get("items") or [])

    def _system_post(self, ring: str, payload: dict[str, Any]) -> dict[str, Any]:
        item = self.DAC.construct_post_item(self.portfolio, self.org, ring, payload)
        return self.DAC.DAM.post_a_b(self.portfolio, self.org, ring, item)

    def _system_put(self, ring: str, idx: str, payload: dict[str, Any]) -> dict[str, Any]:
        item = self.DAC.construct_put_item(self.portfolio, self.org, ring, idx, payload)
        if "error" in item:
            return item
        return self.DAC.DAM.put_a_b_c(self.portfolio, self.org, ring, idx, item)

    def _system_delete(self, ring: str, idx: str) -> dict[str, Any]:
        return self.DAC.DAM.delete_a_b_c(self.portfolio, self.org, ring, idx)

    def _list_ring_raw(self, ring: str) -> list[dict[str, Any]]:
        listed = self.DAC.DAM.get_a_b(self.portfolio, self.org, ring, limit=200)
        return [_attrs(r) for r in (listed.get("items") or [])]

    def resolve_identity(self, external_id: str) -> dict[str, Any] | None:
        external_id = (external_id or "").strip().lower()
        items = self._query(IDENTITIES_RING, f"{CHANNEL}:{external_id}")
        for item in items:
            if str(item.get("channel")) == CHANNEL and str(item.get("external_id")).lower() == external_id:
                return item
        # Fallback scan when query index shape differs
        for item in self._list_ring_raw(IDENTITIES_RING):
            if str(item.get("channel")) == CHANNEL and str(item.get("external_id")).lower() == external_id:
                return item
        return None

    def list_for_user(self, user_id: str, *, authenticated: bool = True) -> list[dict[str, Any]]:
        if authenticated:
            response = self.DAC.get_a_b(self.portfolio, self.org, IDENTITIES_RING, limit=200)
            if response.get("success") is False:
                return []
            items = response.get("items") or []
        else:
            items = self._list_ring_raw(IDENTITIES_RING)
        return [
            i
            for i in items
            if str(i.get("channel")) == CHANNEL and str(i.get("user_id")) == user_id
        ]

    def _iter_pending_code_rows(self) -> list[dict[str, Any]]:
        now = int(time.time())
        rows: list[dict[str, Any]] = []
        for row in self._list_ring_raw(CODES_RING):
            if str(row.get("channel")) != CHANNEL:
                continue
            if str(row.get("consumed_at") or "").strip():
                continue
            try:
                expires_at = int(float(str(row.get("expires_at") or "0")))
            except (TypeError, ValueError):
                expires_at = 0
            if expires_at <= now:
                continue
            rows.append(row)
        return rows

    def has_pending_link_codes(self) -> bool:
        """True when at least one unconsumed, unexpired LINK code is waiting."""
        return bool(self._iter_pending_code_rows())

    def is_valid_pending_code(self, code: str) -> bool:
        """Fast check: code matches a pending row (not consumed, not expired)."""
        if not code:
            return False
        code_hash = hash_link_code(code)
        for row in self._iter_pending_code_rows():
            if str(row.get("code_hash")) == code_hash:
                return True
        # Fallback: query index may be faster for large rings but pending set is tiny.
        items = self._query(CODES_RING, f"{CHANNEL}:{code_hash}")
        for row in items:
            if (
                str(row.get("channel")) == CHANNEL
                and str(row.get("code_hash")) == code_hash
                and not str(row.get("consumed_at") or "").strip()
            ):
                try:
                    expires_at = int(float(str(row.get("expires_at") or "0")))
                except (TypeError, ValueError):
                    expires_at = 0
                return expires_at > now
        return False

    def mint_link_code(self, user_id: str) -> dict[str, Any]:
        code = generate_link_token()
        code_hash = hash_link_code(code)
        expires_at = int(time.time()) + LINK_CODE_TTL_SECONDS

        listed = self.DAC.get_a_b(self.portfolio, self.org, CODES_RING, limit=200)
        pending = listed.get("items") or [] if listed.get("success") else []
        for row in pending:
            if (
                str(row.get("channel")) == CHANNEL
                and str(row.get("user_id")) == user_id
                and not str(row.get("consumed_at") or "").strip()
            ):
                doc_id = row.get("_id")
                if doc_id:
                    self.DAC.put_a_b_c(
                        self.portfolio,
                        self.org,
                        CODES_RING,
                        doc_id,
                        {"expires_at": "0"},
                    )

        doc = {
            "channel": CHANNEL,
            "code_hash": code_hash,
            "user_id": user_id,
            "expires_at": str(expires_at),
            "consumed_at": "",
        }
        response, status = self.DAC.post_a_b(self.portfolio, self.org, CODES_RING, doc)
        if not response.get("success"):
            return {
                "success": False,
                "message": "Could not mint link code",
                "output": response,
                "status": status,
            }
        return {
            "success": True,
            "code": code,
            "expires_at": expires_at,
            "expires_at_iso": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
            "output": response,
        }

    def consume_link_code(
        self,
        *,
        external_id: str,
        code: str,
        display_name: str = "",
    ) -> dict[str, Any]:
        external_id = (external_id or "").strip().lower()
        code_hash = hash_link_code(code)
        items = self._query(CODES_RING, f"{CHANNEL}:{code_hash}")
        row = next(
            (
                i
                for i in items
                if str(i.get("channel")) == CHANNEL
                and str(i.get("code_hash")) == code_hash
                and not str(i.get("consumed_at") or "").strip()
            ),
            None,
        )
        if not row:
            for i in self._list_ring_raw(CODES_RING):
                if (
                    str(i.get("channel")) == CHANNEL
                    and str(i.get("code_hash")) == code_hash
                    and not str(i.get("consumed_at") or "").strip()
                ):
                    row = i
                    break
        if not row:
            return {"status": "invalid"}

        try:
            expires_at = int(float(str(row.get("expires_at") or "0")))
        except (TypeError, ValueError):
            expires_at = 0
        if expires_at <= int(time.time()):
            return {"status": "expired"}

        minter = str(row.get("user_id") or "")
        if not minter:
            return {"status": "invalid"}

        existing = self.resolve_identity(external_id)
        if existing and str(existing.get("user_id")) != minter:
            return {"status": "conflict"}

        doc_id = row.get("_id")
        if not doc_id:
            return {"status": "invalid"}

        claim = self._system_put(CODES_RING, doc_id, {"consumed_at": _now_iso()})
        if claim.get("error"):
            _logger.error("Failed to claim link code: %s", claim)
            return {"status": "invalid"}

        replaced: list[dict[str, str]] = []
        for ident in self._list_ring_raw(IDENTITIES_RING):
            if (
                str(ident.get("channel")) == CHANNEL
                and str(ident.get("user_id")) == minter
                and str(ident.get("external_id")).lower() != external_id
            ):
                rid = ident.get("_id")
                if rid:
                    self._system_delete(IDENTITIES_RING, rid)
                    replaced.append(
                        {
                            "external_id": str(ident.get("external_id") or ""),
                            "display_name": str(ident.get("display_name") or ""),
                        }
                    )

        now = _now_iso()
        if existing and str(existing.get("user_id")) == minter:
            eid = existing.get("_id")
            if eid:
                self._system_put(
                    IDENTITIES_RING,
                    eid,
                    {
                        "display_name": display_name or existing.get("display_name") or "",
                        "last_seen_at": now,
                    },
                )
        else:
            posted = self._system_post(
                IDENTITIES_RING,
                {
                    "channel": CHANNEL,
                    "external_id": external_id,
                    "user_id": minter,
                    "display_name": display_name or "",
                    "linked_at": now,
                    "last_seen_at": now,
                },
            )
            if posted.get("error"):
                _logger.error("Failed to create identity: %s", posted)
                return {"status": "invalid"}

        ActivityLog(self.DAC, self.portfolio).append(
            event_type="identity_linked",
            summary=f"Email linked to user: {external_id}",
            trigger="inbound",
            actor_user_id=str(minter),
            refs={
                "external_id": external_id,
                "user_id": str(minter),
                "replaced_count": len(replaced),
            },
        )
        return {"status": "linked", "user_id": minter, "replaced": replaced}

    def unlink(self, user_id: str, external_id: str | None = None) -> dict[str, Any]:
        identities = self.list_for_user(user_id, authenticated=True)
        removed = []
        for ident in identities:
            if external_id and str(ident.get("external_id")).lower() != external_id.lower():
                continue
            doc_id = ident.get("_id")
            if not doc_id:
                continue
            response, status = self.DAC.delete_a_b_c(
                self.portfolio, self.org, IDENTITIES_RING, doc_id
            )
            removed.append(
                {"id": doc_id, "success": bool(response.get("success")), "status": status}
            )
            if response.get("success"):
                ActivityLog(self.DAC, self.portfolio).append(
                    event_type="identity_unlinked",
                    summary=f"Email unlinked: {ident.get('external_id') or external_id}",
                    trigger="manual",
                    actor_user_id=str(user_id),
                    refs={
                        "external_id": str(ident.get("external_id") or external_id or ""),
                        "user_id": str(user_id),
                    },
                )
        return {"success": True, "removed": removed}

    def touch_last_seen(self, identity: dict[str, Any]) -> None:
        doc_id = identity.get("_id")
        if not doc_id:
            return
        try:
            self._system_put(IDENTITIES_RING, doc_id, {"last_seen_at": _now_iso()})
        except Exception as exc:
            _logger.warning("touch_last_seen failed: %s", exc)


def extract_code_from_text(text: str) -> str | None:
    return extract_link_code(text)
