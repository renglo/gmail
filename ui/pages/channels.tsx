import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

interface AgentProps {
  portfolio: string;
  org: string;
  tool: string;
  tree?: { portfolios: Record<string, unknown> };
  onNavigate: (path: string) => void;
}

type Identity = {
  _id?: string;
  channel?: string;
  external_id?: string;
  display_name?: string;
  linked_at?: string;
  last_seen_at?: string;
};

type MintResult = {
  code: string;
  agentEmail: string;
  instructions: string;
  expiresAt: string;
};

function unwrapHandlerOutput(data: unknown): Record<string, unknown> {
  if (!data || typeof data !== "object") return {};
  const root = data as Record<string, unknown>;
  const output = root.output;
  if (output && typeof output === "object") {
    const inner = output as Record<string, unknown>;
    if (
      inner.output &&
      typeof inner.output === "object" &&
      ("code" in (inner.output as object) || "items" in (inner.output as object))
    ) {
      return inner.output as Record<string, unknown>;
    }
    if ("code" in inner || "items" in inner || "success" in inner) {
      return inner;
    }
  }
  return root;
}

export default function GmailChannels({ portfolio, org, tool }: AgentProps) {
  const [identities, setIdentities] = useState<Identity[]>([]);
  const [loading, setLoading] = useState(true);
  const [minting, setMinting] = useState(false);
  const [mint, setMint] = useState<MintResult | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [error, setError] = useState<string | null>(null);
  const baselineRef = useRef<Set<string>>(new Set());

  const apiBase = import.meta.env.VITE_API_URL;
  const authHeaders = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${sessionStorage.accessToken}`,
  };

  const loadIdentities = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/_schd/${portfolio}/${org}/call/gmail/identities`, {
        method: "POST",
        headers: authHeaders,
        body: JSON.stringify({ action: "list", portfolio, org }),
      });
      if (!res.ok) {
        setError("Could not load linked identities");
        return [];
      }
      setError(null);
      const data = await res.json();
      const unwrapped = unwrapHandlerOutput(data);
      const items = (unwrapped.items as Identity[]) || (unwrapped.output as Identity[]) || [];
      const list = Array.isArray(items) ? items : [];
      setIdentities(list);
      return list;
    } catch {
      setError("Could not load linked identities");
      return [];
    } finally {
      setLoading(false);
    }
  }, [apiBase, portfolio, org]);

  useEffect(() => {
    void loadIdentities();
  }, [loadIdentities]);

  useEffect(() => {
    if (!mint) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [mint]);

  useEffect(() => {
    if (!mint) return;
    let stopped = false;

    const pollMailbox = async () => {
      try {
        await fetch(`${apiBase}/_schd/${portfolio}/${org}/call/gmail/poll_inbox`, {
          method: "POST",
          headers: authHeaders,
          body: JSON.stringify({
            portfolio,
            org,
            trigger: "connect_wait",
            link_code: mint.code,
          }),
        });
      } catch {
        // Non-fatal — identity poll below still runs.
      }
    };

    const poll = async () => {
      await pollMailbox();
      const list = await loadIdentities();
      const linked = list.some(
        (i) => i.channel === "gmail" && !baselineRef.current.has(String(i.external_id || "")),
      );
      if (linked && !stopped) {
        setMint(null);
      }
    };

    void poll();
    const iv = setInterval(() => {
      void poll();
    }, 4000);
    return () => {
      stopped = true;
      clearInterval(iv);
    };
  }, [mint, loadIdentities, apiBase, portfolio, org]);

  useEffect(() => {
    if (mint && Date.parse(mint.expiresAt) <= now) {
      setMint(null);
    }
  }, [mint, now]);

  async function connect() {
    setMinting(true);
    setError(null);
    setMint(null);
    baselineRef.current = new Set(
      identities.map((i) => String(i.external_id || "")).filter(Boolean),
    );

    try {
      const res = await fetch(`${apiBase}/_schd/${portfolio}/${org}/call/gmail/mint_link`, {
        method: "POST",
        headers: authHeaders,
        body: JSON.stringify({ portfolio, org }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError("Failed to mint link code — connect the agent inbox in Settings first");
        return;
      }
      const unwrapped = unwrapHandlerOutput(data);
      const code = String(unwrapped.code || "");
      const expiresAt = String(unwrapped.expiresAt || unwrapped.expires_at_iso || "");
      const agentEmail = String(unwrapped.agent_email || "");
      const instructions = String(unwrapped.instructions || "");
      if (!code || !expiresAt) {
        setError("Mint response incomplete");
        return;
      }
      setMint({ code, agentEmail, instructions, expiresAt });
    } catch {
      setError("Failed to mint link code");
    } finally {
      setMinting(false);
    }
  }

  async function unlink(externalId?: string) {
    await fetch(`${apiBase}/_schd/${portfolio}/${org}/call/gmail/identities`, {
      method: "POST",
      headers: authHeaders,
      body: JSON.stringify({ action: "unlink", portfolio, org, external_id: externalId }),
    });
    await loadIdentities();
  }

  const secondsLeft = mint
    ? Math.max(0, Math.floor((Date.parse(mint.expiresAt) - now) / 1000))
    : 0;

  const mailto = mint?.agentEmail
    ? `mailto:${encodeURIComponent(mint.agentEmail)}?subject=${encodeURIComponent(mint.code)}&body=${encodeURIComponent(`Please connect my account: ${mint.code}`)}`
    : null;

  return (
    <div className="mx-auto w-full max-w-2xl p-4 sm:p-6">
      <Card>
        <CardHeader>
          <CardTitle>Connect email</CardTitle>
          <CardDescription>
            Link your personal email to your Renglo account by sending a one-time LINK code to this
            portfolio’s shared agent inbox. Tool: {tool}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
          {error && <p className="text-sm text-red-600">{error}</p>}

          {identities.length > 0 ? (
            <div className="space-y-3">
              <p className="text-sm font-medium text-emerald-700">Connected ✓</p>
              {identities.map((identity) => (
                <div
                  key={identity._id || identity.external_id}
                  className="flex items-center justify-between rounded-md border border-border px-3 py-2"
                >
                  <div>
                    <div className="text-sm font-medium">
                      {identity.display_name || identity.external_id}
                    </div>
                    <div className="text-xs text-muted-foreground">{identity.external_id}</div>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void unlink(identity.external_id)}
                  >
                    Unlink
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No email address linked yet.</p>
          )}

          {!mint ? (
            <Button onClick={() => void connect()} disabled={minting}>
              {minting ? "Preparing…" : identities.length ? "Switch email" : "Connect email"}
            </Button>
          ) : (
            <div className="space-y-4 rounded-md border border-border p-4">
              <p className="text-sm">
                Waiting for your email… expires in {secondsLeft}s
              </p>
              {mint.agentEmail && (
                <p className="text-sm">
                  Send to <span className="font-medium">{mint.agentEmail}</span>
                </p>
              )}
              <p className="whitespace-pre-wrap text-sm text-muted-foreground">{mint.instructions}</p>
              <p className="break-all font-mono text-xs">{mint.code}</p>
              {mailto && (
                <a
                  href={mailto}
                  className="text-sm text-blue-700 underline"
                >
                  Open mail client
                </a>
              )}
              <Button variant="ghost" size="sm" onClick={() => setMint(null)}>
                Cancel
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
