import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface AgentProps {
  portfolio: string;
  org: string;
  tool: string;
  tree?: { portfolios: Record<string, unknown> };
  onNavigate: (path: string) => void;
}

const SINGLETON_ID = "00000000-0000-0000-0000-000000000000";
/** Portfolio-wide config ring (same as WhatsApp). */
const CONFIG_ORG = "_all";

type ConfigForm = {
  oauth_client_id: string;
  oauth_client_secret: string;
  oauth_state_secret: string;
  agent_handler: string;
  enabled: string;
  poll_batch_size: string;
};

const EMPTY: ConfigForm = {
  oauth_client_id: "",
  oauth_client_secret: "",
  oauth_state_secret: "",
  agent_handler: "dumbo/generic_agent",
  enabled: "true",
  poll_batch_size: "25",
};

function unwrapHandlerOutput(data: unknown): Record<string, unknown> {
  if (!data || typeof data !== "object") return {};
  const root = data as Record<string, unknown>;
  const output = root.output;
  if (output && typeof output === "object") {
    const inner = output as Record<string, unknown>;
    if (inner.output && typeof inner.output === "object" && "auth_url" in (inner.output as object)) {
      return inner.output as Record<string, unknown>;
    }
    if ("auth_url" in inner || "success" in inner) {
      return inner;
    }
  }
  return root;
}

export default function GmailSettings({ portfolio, org }: AgentProps) {
  const [form, setForm] = useState<ConfigForm>(EMPTY);
  const [connectedEmail, setConnectedEmail] = useState("");
  const [hasRefresh, setHasRefresh] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [persisted, setPersisted] = useState<ConfigForm>(EMPTY);

  const apiBase = import.meta.env.VITE_API_URL;
  const path = `${apiBase}/_data/${portfolio}/${CONFIG_ORG}/gmail_config/${SINGLETON_ID}`;
  const authHeaders = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${sessionStorage.accessToken}`,
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(path, {
        headers: { Authorization: `Bearer ${sessionStorage.accessToken}` },
      });
      if (!res.ok) {
        setError("Could not load gmail_config — run Install first");
        setForm(EMPTY);
        return;
      }
      const data = await res.json();
      const next: ConfigForm = {
        oauth_client_id: String(data.oauth_client_id || ""),
        oauth_client_secret: String(data.oauth_client_secret || ""),
        oauth_state_secret: String(data.oauth_state_secret || ""),
        agent_handler: String(data.agent_handler || "dumbo/generic_agent"),
        enabled: String(data.enabled ?? "true"),
        poll_batch_size: String(data.poll_batch_size || "25"),
      };
      setForm(next);
      setPersisted(next);
      setConnectedEmail(String(data.email || ""));
      setHasRefresh(Boolean(String(data.refresh_token || "").trim()));
    } catch {
      setError("Could not load gmail_config");
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const gmail = params.get("gmail");
    if (gmail === "linked") {
      setMessage(`Connected as ${params.get("as") || "mailbox"}`);
      void load();
    } else if (gmail === "error") {
      setError(params.get("error") || "OAuth failed");
    }
  }, [load]);

  async function save() {
    setSaving(true);
    setMessage(null);
    setError(null);
    try {
      const res = await fetch(path, {
        method: "PUT",
        headers: authHeaders,
        body: JSON.stringify({
          oauth_client_id: form.oauth_client_id,
          oauth_client_secret: form.oauth_client_secret,
          oauth_state_secret: form.oauth_state_secret,
          agent_handler: form.agent_handler,
          enabled: form.enabled,
          poll_batch_size: form.poll_batch_size,
        }),
      });
      if (!res.ok) {
        setError("Save failed");
        return;
      }
      setMessage("Saved");
      await load();
    } catch {
      setError("Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function connect() {
    setConnecting(true);
    setError(null);
    setMessage(null);
    try {
      const res = await fetch(`${apiBase}/_schd/${portfolio}/${org}/call/gmail/oauth_connect`, {
        method: "POST",
        headers: authHeaders,
        body: JSON.stringify({
          portfolio,
          org,
          return_url: `${window.location.origin}${window.location.pathname}`,
        }),
      });
      const data = await res.json().catch(() => ({}));
      const unwrapped = unwrapHandlerOutput(data);
      const authUrl = String(unwrapped.auth_url || "");
      if (!res.ok || !authUrl) {
        setError(
          String(
            unwrapped.message ||
              data.message ||
              "Connect failed — set this portfolio's OAuth client id, client secret, and state secret, then save",
          ),
        );
        return;
      }
      window.location.href = authUrl;
    } catch {
      setError("Connect failed");
    } finally {
      setConnecting(false);
    }
  }

  async function disconnect() {
    setConnecting(true);
    setError(null);
    try {
      await fetch(`${apiBase}/_schd/${portfolio}/${org}/call/gmail/oauth_disconnect`, {
        method: "POST",
        headers: authHeaders,
        body: JSON.stringify({ portfolio, org }),
      });
      setMessage("Disconnected");
      await load();
    } catch {
      setError("Disconnect failed");
    } finally {
      setConnecting(false);
    }
  }

  async function pollNow() {
    setMessage(null);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/_schd/${portfolio}/${org}/call/gmail/poll_inbox`, {
        method: "POST",
        headers: authHeaders,
        body: JSON.stringify({ portfolio, org, trigger: "manual" }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError("Poll failed");
        return;
      }
      setMessage(`Poll completed (count=${data?.output?.count ?? data?.count ?? "?"})`);
    } catch {
      setError("Poll failed");
    }
  }

  const oauthDirty =
    form.oauth_client_id !== persisted.oauth_client_id ||
    form.oauth_client_secret !== persisted.oauth_client_secret ||
    form.oauth_state_secret !== persisted.oauth_state_secret;
  const oauthReady =
    !oauthDirty &&
    Boolean(persisted.oauth_client_id.trim()) &&
    Boolean(persisted.oauth_client_secret.trim()) &&
    Boolean(persisted.oauth_state_secret.trim());

  function field(key: keyof ConfigForm, label: string, hint?: string, type = "text") {
    return (
      <div className="space-y-1.5">
        <Label htmlFor={key}>{label}</Label>
        <Input
          id={key}
          type={type}
          value={form[key]}
          onChange={(e) => setForm((prev) => ({ ...prev, [key]: e.target.value }))}
        />
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-2xl p-4 sm:p-6">
      <Card>
        <CardHeader>
          <CardTitle>Gmail settings</CardTitle>
          <CardDescription>
            Connect this portfolio’s shared agent inbox with Google. Each portfolio keeps its own OAuth client and state secret.
            After consent, the browser returns to this page.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
          {error && <p className="text-sm text-red-600">{error}</p>}
          {message && <p className="text-sm text-emerald-700">{message}</p>}

          {!loading && (
            <>
              <div className="rounded-md border border-border px-3 py-2 text-sm">
                {hasRefresh ? (
                  <span className="text-emerald-700">
                    Connected ✓ {connectedEmail || "mailbox"}
                  </span>
                ) : (
                  <span className="text-muted-foreground">
                    Agent mailbox not connected — sign in as the agent address (any domain) and
                    approve Gmail access
                  </span>
                )}
              </div>

              {field(
                "oauth_client_id",
                "OAuth client id",
                "Google OAuth Web client id for this portfolio",
                "password",
              )}
              {field(
                "oauth_client_secret",
                "OAuth client secret",
                "Google OAuth Web client secret for this portfolio",
                "password",
              )}
              {field(
                "oauth_state_secret",
                "OAuth state secret",
                "HMAC secret for this portfolio's OAuth state. Save before Connect.",
                "password",
              )}
              {field(
                "agent_handler",
                "Agent handler",
                "extension/handler for linked messages (default dumbo/generic_agent)",
              )}
              {field("enabled", "Enabled", "true / false — when false, poller skips processing")}
              {field(
                "poll_batch_size",
                "Poll batch size",
                "Max unread messages per poll (1–100). Use 1 when troubleshooting.",
                "number",
              )}

              <div className="flex flex-wrap gap-2">
                <Button onClick={() => void save()} disabled={saving}>
                  {saving ? "Saving…" : "Save"}
                </Button>
                <Button variant="default" onClick={() => void connect()} disabled={connecting || !oauthReady}>
                  {connecting ? "…" : hasRefresh ? "Re-connect mailbox" : "Connect agent inbox"}
                </Button>
                {!oauthReady && (
                  <p className="w-full text-xs text-muted-foreground">
                    Save this portfolio's OAuth client id, client secret, and state secret before Connect.
                  </p>
                )}
                {hasRefresh && (
                  <Button variant="outline" onClick={() => void disconnect()} disabled={connecting}>
                    Disconnect
                  </Button>
                )}
                <Button variant="secondary" onClick={() => void pollNow()}>
                  Poll now
                </Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
