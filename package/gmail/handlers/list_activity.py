"""List Gmail activity entries and optional S3 detail payloads."""

from __future__ import annotations

from typing import Any, Dict

from renglo.common import load_config
from renglo.data.data_controller import DataController

from .activity_log import ActivityLog


class ListActivity:
    def __init__(self) -> None:
        config = load_config()
        self.DAC = DataController(config=config)

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        portfolio = str(payload.get("portfolio") or "")
        if not portfolio:
            return {"success": False, "message": "portfolio required"}

        log = ActivityLog(self.DAC, portfolio)
        event_id = str(payload.get("event_id") or "").strip()
        detail_path = str(payload.get("detail_s3_path") or "").strip()

        if event_id or detail_path:
            path = detail_path
            if not path and event_id:
                days = int(payload.get("days") or 14)
                items = log.list_recent(days=days, limit=500)
                match = next((i for i in items if str(i.get("event_id")) == event_id), None)
                if not match:
                    return {"success": False, "message": "event not found"}
                path = str(match.get("detail_s3_path") or "")
                if not path:
                    return {
                        "success": True,
                        "action": "get_activity_detail",
                        "entry": match,
                        "detail": None,
                    }
            detail = log.load_detail(path)
            return {
                "success": True,
                "action": "get_activity_detail",
                "detail_s3_path": path,
                "detail": detail,
            }

        days = int(payload.get("days") or 7)
        limit = int(payload.get("limit") or 100)
        event_type = str(payload.get("event_type") or "")
        items = log.list_recent(days=days, limit=limit, event_type=event_type)
        return {
            "success": True,
            "action": "list_activity",
            "count": len(items),
            "items": items,
            "output": {"count": len(items), "items": items},
        }
