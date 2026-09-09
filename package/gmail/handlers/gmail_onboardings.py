"""Install Gmail tool, schd entries, singleton config, and poll cron job."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from flask import current_app

from renglo.auth.auth_controller import AuthController
from renglo.blueprint.blueprint_controller import BlueprintController
from renglo.common import load_config
from renglo.data.data_controller import DataController
from renglo.schd.schd_controller import SchdController

from .config import CONFIG_ORG, ConfigStore

_logger = logging.getLogger(__name__)

REQUIRED_BLUEPRINT_RINGS = (
    "gmail_config",
    "gmail_activity",
    "channel_link_codes",
    "channel_identities",
)


class GmailOnboardings:
    """Install Gmail tool, schd entries, singleton config, and optional poll cron."""

    def __init__(self) -> None:
        config = load_config()
        self.config = config
        self.DAC = DataController(config=config)
        self.AUC = AuthController(config=config)
        self.BPC = BlueprintController(config=config)
        self.SHC = SchdController(config=config)
        self.bridge: Dict[str, Any] = {}

    def _blueprint_present(self, ring: str) -> bool:
        blueprint = self.BPC.get_blueprint("irma", ring, "last")
        return (
            isinstance(blueprint, dict)
            and blueprint.get("success") is not False
            and "fields" in blueprint
        )

    def _extension_blueprints_present(self) -> bool:
        return all(self._blueprint_present(ring) for ring in REQUIRED_BLUEPRINT_RINGS)

    def ensure_extension_initialized(
        self, portfolio: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        action = "ensure_extension_initialized"
        if self._extension_blueprints_present():
            return {
                "success": True,
                "action": action,
                "message": "Extension blueprints already present",
            }

        from .initialize_extension import InitializeExtension

        init_payload = {
            "portfolio": portfolio,
            "org": str(payload.get("org") or CONFIG_ORG).strip() or CONFIG_ORG,
        }
        return InitializeExtension().run(init_payload)

    def create_tool(self, portfolio: str, tool: str, handle: str) -> Dict[str, Any]:
        action = "create_tool"
        current_app.logger.debug("Installing Gmail tool in portfolio")

        kwargs = {
            "name": tool,
            "handle": handle,
            "portfolio_id": portfolio,
        }
        response = self.AUC.create_entity("tool", **kwargs)
        self.bridge["tool_id"] = response.get("document", {}).get("_id")

        if not response.get("success"):
            return {
                "success": False,
                "action": action,
                "message": "Could not install tool",
                "input": kwargs,
                "output": response,
            }
        return {
            "success": True,
            "action": action,
            "message": "Tool installed",
            "input": kwargs,
            "output": response,
        }

    def create_schd_tool_doc(self, portfolio: str, org: str, doc: Dict[str, Any]) -> Dict[str, Any]:
        action = "create_schd_tool_doc"
        response, _status = self.DAC.post_a_b(portfolio, org, "schd_tools", doc)
        if not response.get("success"):
            return {
                "success": False,
                "action": action,
                "message": "Could not register schd tool",
                "input": doc,
                "output": response,
            }
        return {
            "success": True,
            "action": action,
            "message": "Scheduler tool registered",
            "input": doc,
            "output": response,
        }

    def create_poll_job(self, portfolio: str, org: str) -> Dict[str, Any]:
        action = "create_poll_job"
        job = {
            "name": "Gmail Poll Inbox",
            "handler": "gmail/poll_inbox",
            "goal": "Poll the agent Gmail inbox for linked inbound mail",
            "schedule_hint": "rate(2 minutes)",
        }
        response, status = self.DAC.post_a_b(portfolio, org, "schd_jobs", job)
        if not response.get("success"):
            return {
                "success": False,
                "action": action,
                "message": "Could not create schd_jobs poll document",
                "output": response,
                "status": status,
            }
        job_id = ""
        path = response.get("path") or ""
        if path:
            job_id = path.rstrip("/").split("/")[-1]
        self.bridge["schd_jobs_id"] = job_id
        return {
            "success": True,
            "action": action,
            "schd_jobs_id": job_id,
            "output": response,
        }

    def create_poll_rule(self, portfolio: str, org: str, schd_jobs_id: str) -> Dict[str, Any]:
        action = "create_poll_rule"
        if not schd_jobs_id:
            return {"success": False, "action": action, "message": "missing schd_jobs_id"}
        event_payload = {
            "portfolio": portfolio,
            "org": org,
            "schd_jobs_id": schd_jobs_id,
            "trigger": "cron",
            "author": "gmail_onboardings",
        }
        try:
            result = self.SHC.create_rule(
                portfolio,
                org,
                "gmail_poll",
                "rate(2 minutes)",
                event_payload,
            )
            return {
                "success": bool(result.get("success")),
                "action": action,
                "message": "Poll cron rule created"
                if result.get("success")
                else "Poll cron rule failed (EventBridge may need ROLE_ARN / API_GATEWAY_ARN)",
                "output": result,
            }
        except Exception as exc:
            _logger.warning("create_poll_rule failed: %s", exc)
            return {
                "success": False,
                "action": action,
                "message": str(exc),
            }

    def refresh_tree(self) -> Dict[str, Any]:
        action = "refresh_tree"
        response = self.AUC.refresh_tree()
        if not response.get("success"):
            return {
                "success": False,
                "action": action,
                "message": "Tree could not be generated",
                "input": [],
                "output": response,
            }
        return {
            "success": True,
            "action": action,
            "message": "The tree has been generated",
            "input": [],
            "output": response,
        }

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        results: List[Dict[str, Any]] = []

        portfolio = str(payload.get("portfolio") or "")
        if not portfolio:
            return {"success": False, "output": "No portfolio selected"}

        # Portfolio-wide install (same pattern as WhatsApp): config + schd at _all.
        org = CONFIG_ORG

        init_step = self.ensure_extension_initialized(portfolio, payload)
        results.append(init_step)
        if not init_step.get("success"):
            return {"success": False, "output": results}

        response_tool = self.create_tool(portfolio, "Gmail", "gmail")
        results.append(response_tool)
        if not response_tool["success"]:
            return {"success": False, "output": results}

        for doc in (
            {
                "key": "gmail_poll_inbox",
                "name": "Gmail Poll Inbox",
                "goal": "Poll agent inbox: LINK gate + agent + threaded reply",
                "handler": "gmail/poll_inbox",
                "init": "_",
                "instructions": "Called by EventBridge cron or manual Poll now.",
                "input": "{}",
                "output": "_",
            },
            {
                "key": "gmail_mint_link",
                "name": "Gmail Mint Link",
                "goal": "Mint a LINK token for binding a personal email",
                "handler": "gmail/mint_link",
                "init": "_",
                "instructions": "Authenticated console Connect email flow.",
                "input": "{}",
                "output": "_",
            },
            {
                "key": "gmail_oauth_connect",
                "name": "Gmail OAuth Connect",
                "goal": "Start Google OAuth for the agent mailbox",
                "handler": "gmail/oauth_connect",
                "init": "_",
                "instructions": "Uses platform GOOGLE_OAUTH_CLIENT_ID/SECRET; stores mailbox tokens on gmail_config.",
                "input": "{}",
                "output": "_",
            },
            {
                "key": "gmail_oauth_disconnect",
                "name": "Gmail OAuth Disconnect",
                "goal": "Revoke and clear agent mailbox tokens",
                "handler": "gmail/oauth_disconnect",
                "init": "_",
                "instructions": "",
                "input": "{}",
                "output": "_",
            },
            {
                "key": "gmail_list_activity",
                "name": "Gmail List Activity",
                "goal": "List Gmail operational activity entries",
                "handler": "gmail/list_activity",
                "init": "_",
                "instructions": "Console activity page; optional event_id for S3 detail.",
                "input": '{"days":7,"limit":100}',
                "output": "_",
            },
            {
                "key": "gmail_reply_message",
                "name": "Gmail Reply Message",
                "goal": "Send a threaded reply from the agent mailbox",
                "handler": "gmail/reply_message",
                "init": "_",
                "instructions": "",
                "input": '{"to":"...","message":"...","thread_id":"..."}',
                "output": "_",
            },
        ):
            response_schd = self.create_schd_tool_doc(portfolio, org, doc)
            results.append(response_schd)
            if not response_schd["success"]:
                return {"success": False, "output": results}

        cfg = ConfigStore(self.DAC, portfolio, org).ensure_defaults()
        results.append(cfg)
        if not cfg.get("success"):
            return {"success": False, "output": results}

        job = self.create_poll_job(portfolio, org)
        results.append(job)
        if job.get("success") and job.get("schd_jobs_id"):
            rule = self.create_poll_rule(portfolio, org, job["schd_jobs_id"])
            results.append(rule)
            # Cron failure is non-fatal — manual poll still works.
        elif not job.get("success"):
            _logger.warning("schd_jobs create failed; continuing without cron")

        response_tree = self.refresh_tree()
        results.append(response_tree)
        if not response_tree["success"]:
            return {"success": False, "output": results}

        return {
            "success": True,
            "message": "run completed",
            "input": payload,
            "output": results,
        }
