"""Gmail activity traceability — daily index in DynamoDB + optional S3 detail blobs."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from renglo.common import load_config
from renglo.files.files_model import FilesModel

from .config import CONFIG_ORG

_logger = logging.getLogger(__name__)

RING = "gmail_activity"
S3_RING_PREFIX = "gmail_activity"
MAX_SUBJECT_LEN = 240
_META_KEYS = frozenset(
    {
        "portfolio_index",
        "doc_index",
        "added",
        "modified",
        "license",
        "public",
        "blueprint",
        "portfolio",
        "org",
        "ring",
        "blueprint_version",
        "path_index",
        "attributes",
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _truncate(text: str, limit: int = MAX_SUBJECT_LEN) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _row_as_view(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Dynamo row (proper or legacy flat) into blueprint fields."""
    if not isinstance(row, dict):
        return {}
    attrs = row.get("attributes")
    if isinstance(attrs, dict) and attrs:
        view = dict(attrs)
    else:
        view = {k: v for k, v in row.items() if k not in _META_KEYS}
    if row.get("_id"):
        view["_id"] = row["_id"]
    return view


def _is_proper_row(row: dict[str, Any]) -> bool:
    return isinstance(row.get("attributes"), dict) and bool(row["attributes"])


def _flatten_entries(raw: Any) -> list[dict[str, Any]]:
    """
    Normalize ``entries`` to a flat list of activity row dicts.

    Legacy docs used blueprint ``type: array`` + ``cardinality: multiple``, which
    wrapped the whole list as a single array element (double nesting).
    """
    if not isinstance(raw, list):
        return []
    flat: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            flat.append(item)
        elif isinstance(item, list):
            for sub in item:
                if isinstance(sub, dict):
                    flat.append(sub)
    return flat


class ActivityLog:
    """
    Append-only Gmail activity ledger.

    - Compact entries live in ring ``gmail_activity``, one document per UTC calendar day.
    - Large payloads (poll classifications) are stored in S3 under
      ``_files/{portfolio}/_all/gmail_activity/{date}/{event_id}.json``.
    """

    def __init__(self, data_controller: Any, portfolio: str) -> None:
        self.DAC = data_controller
        self.portfolio = portfolio
        self.org = CONFIG_ORG
        config = load_config()
        self._files = FilesModel(config=config)

    def _raw_day_row(self, journal_date: str) -> dict[str, Any] | None:
        try:
            row = self.DAC.DAM.get_a_b_c(self.portfolio, self.org, RING, journal_date)
            if not row or row.get("error"):
                return None
            return row
        except Exception as exc:
            _logger.warning("activity raw day row failed: %s", exc)
            return None

    def _get_day_doc(self, journal_date: str) -> dict[str, Any] | None:
        row = self._raw_day_row(journal_date)
        if not row:
            return None
        view = _row_as_view(row)
        if not view:
            return None
        view["_id"] = row.get("_id") or journal_date
        view["entries"] = _flatten_entries(view.get("entries"))
        return view

    def _create_day_doc(self, journal_date: str, entries: list[dict[str, Any]]) -> bool:
        entries = _flatten_entries(entries)
        payload = {"journal_date": journal_date, "entries": entries}
        try:
            item = self.DAC.construct_post_item(self.portfolio, self.org, RING, payload)
            if not isinstance(item, dict) or item.get("error"):
                return False
            item["_id"] = journal_date
            result = self.DAC.DAM.post_a_b(self.portfolio, self.org, RING, item)
            return "error" not in result
        except Exception as exc:
            _logger.warning("activity create day doc failed: %s", exc)
            return False

    def _migrate_flat_day_doc(self, journal_date: str, row: dict[str, Any]) -> bool:
        """Replace legacy flat rows (missing attributes) with blueprint-shaped docs."""
        view = _row_as_view(row)
        entries = _flatten_entries(view.get("entries"))
        try:
            deleted = self.DAC.DAM.delete_a_b_c(self.portfolio, self.org, RING, journal_date)
            if isinstance(deleted, dict) and deleted.get("error"):
                _logger.warning("activity flat doc delete failed: %s", deleted)
        except Exception as exc:
            _logger.warning("activity flat doc delete failed: %s", exc)
        return self._create_day_doc(journal_date, entries)

    def _put_day_entries(self, journal_date: str, entries: list[dict[str, Any]]) -> bool:
        entries = _flatten_entries(entries)
        payload = {"journal_date": journal_date, "entries": entries}
        try:
            raw = self._raw_day_row(journal_date)
            if raw and not _is_proper_row(raw):
                if not self._migrate_flat_day_doc(journal_date, raw):
                    return False
                raw = self._raw_day_row(journal_date)

            if raw:
                item = self.DAC.construct_put_item(
                    self.portfolio, self.org, RING, journal_date, payload
                )
                if not isinstance(item, dict) or item.get("error"):
                    return False
                result = self.DAC.DAM.put_a_b_c(
                    self.portfolio, self.org, RING, journal_date, item
                )
                return "error" not in result

            return self._create_day_doc(journal_date, entries)
        except Exception as exc:
            _logger.warning("activity put day entries failed: %s", exc)
            return False

    def _store_detail_s3(self, journal_date: str, event_id: str, detail: dict[str, Any]) -> str:
        ring = f"{S3_RING_PREFIX}/{journal_date}"
        body = json.dumps(detail, default=str).encode("utf-8")
        try:
            resp = self._files.a_b_post(
                self.portfolio,
                self.org,
                ring,
                body,
                "application/json",
                event_id,
            )
            if resp.get("success") and resp.get("path"):
                return str(resp["path"])
        except Exception as exc:
            _logger.warning("activity S3 detail write failed: %s", exc)
        return ""

    def append(
        self,
        *,
        event_type: str,
        summary: str,
        trigger: str = "",
        actor_user_id: str = "",
        refs: Optional[dict[str, Any]] = None,
        detail: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Best-effort append; never raises."""
        journal_date = _today()
        event_id = str(uuid.uuid4())
        entry: dict[str, Any] = {
            "event_id": event_id,
            "ts": _now_iso(),
            "event_type": event_type,
            "summary": _truncate(summary, 500),
            "trigger": trigger or "",
            "actor_user_id": actor_user_id or "",
            "refs": refs or {},
        }
        if detail:
            s3_path = self._store_detail_s3(journal_date, event_id, detail)
            if s3_path:
                entry["detail_s3_path"] = s3_path

        try:
            doc = self._get_day_doc(journal_date)
            entries = list((doc or {}).get("entries") or [])
            entries.append(entry)
            if not self._put_day_entries(journal_date, entries):
                _logger.warning("activity index append failed for %s", event_type)
        except Exception as exc:
            _logger.warning("activity append failed: %s", exc)

        return entry

    def load_detail(self, detail_s3_path: str) -> dict[str, Any] | None:
        """Load JSON detail blob from S3 path returned on an activity entry."""
        path = (detail_s3_path or "").strip()
        if not path.startswith("_files/"):
            return None
        parts = path.split("/")
        # _files/{portfolio}/{org}/{ring...}/{filename}
        if len(parts) < 6:
            return None
        portfolio, org = parts[1], parts[2]
        ring_parts = parts[3:-1]
        ring = "/".join(ring_parts)
        filename = parts[-1]
        try:
            resp = self._files.a_b_c_get(portfolio, org, ring, filename)
            if not resp.get("success"):
                return None
            raw = resp.get("content")
            if isinstance(raw, (bytes, bytearray)):
                return json.loads(raw.decode("utf-8"))
            if isinstance(raw, str):
                return json.loads(raw)
        except Exception as exc:
            _logger.warning("activity detail load failed: %s", exc)
        return None

    def list_recent(
        self,
        *,
        days: int = 7,
        limit: int = 100,
        event_type: str = "",
    ) -> list[dict[str, Any]]:
        """Flatten recent daily docs, newest first."""
        days = max(1, min(int(days or 7), 31))
        limit = max(1, min(int(limit or 100), 500))
        event_filter = (event_type or "").strip()

        listed = self.DAC.DAM.get_a_b(self.portfolio, self.org, RING, limit=days + 5)
        docs = list(listed.get("items") or [])

        def _doc_date(doc: dict[str, Any]) -> str:
            view = _row_as_view(doc)
            return str(view.get("journal_date") or view.get("_id") or "")

        docs.sort(key=_doc_date, reverse=True)
        docs = docs[:days]

        flat: list[dict[str, Any]] = []
        for doc in docs:
            view = _row_as_view(doc)
            for row in _flatten_entries(view.get("entries")):
                if event_filter and str(row.get("event_type") or "") != event_filter:
                    continue
                flat.append(row)
        flat.sort(key=lambda e: str(e.get("ts") or ""), reverse=True)
        return flat[:limit]

    @staticmethod
    def message_ref(msg: dict[str, Any], handled: dict[str, Any]) -> dict[str, Any]:
        """Compact reference for a polled message (no body stored)."""
        action = str(handled.get("action") or handled.get("reason") or "unknown")
        ref: dict[str, Any] = {
            "msg_id": str(msg.get("msg_id") or ""),
            "thread_id": str(msg.get("thread_id") or ""),
            "internal_ms": int(msg.get("internal_ms") or 0),
            "from": _truncate(str(msg.get("from") or ""), 120),
            "subject": _truncate(str(msg.get("subject") or ""), MAX_SUBJECT_LEN),
            "action": action,
        }
        if handled.get("external_id"):
            ref["external_id"] = str(handled["external_id"])
        if handled.get("user_id"):
            ref["user_id"] = str(handled["user_id"])
        if handled.get("status"):
            ref["link_status"] = str(handled["status"])
        if handled.get("reply"):
            ref["reply_sent"] = True
        elif action == "agent":
            ref["reply_sent"] = bool(handled.get("send"))
        if handled.get("reason"):
            ref["skip_reason"] = str(handled["reason"])
        return ref
