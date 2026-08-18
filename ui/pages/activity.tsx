import { Fragment, useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface AgentProps {
  portfolio: string;
  org: string;
  tool: string;
  tree?: { portfolios: Record<string, unknown> };
  onNavigate: (path: string) => void;
}

type ActivityEntry = {
  event_id?: string;
  ts?: string;
  event_type?: string;
  summary?: string;
  trigger?: string;
  actor_user_id?: string;
  refs?: Record<string, unknown>;
  detail_s3_path?: string;
};

const EVENT_TYPES = [
  { value: "all", label: "All events" },
  { value: "poll", label: "Poll" },
  { value: "poll_skipped", label: "Poll skipped" },
  { value: "poll_error", label: "Poll error" },
  { value: "mailbox_connected", label: "Mailbox connected" },
  { value: "mailbox_disconnected", label: "Mailbox disconnected" },
  { value: "identity_linked", label: "Email linked" },
  { value: "identity_unlinked", label: "Email unlinked" },
];

function unwrapHandlerOutput(data: unknown): Record<string, unknown> {
  if (!data || typeof data !== "object") return {};
  const root = data as Record<string, unknown>;
  const output = root.output;
  if (output && typeof output === "object") {
    const inner = output as Record<string, unknown>;
    if (Array.isArray(inner.items)) return inner;
    if (inner.output && typeof inner.output === "object") {
      return inner.output as Record<string, unknown>;
    }
    if ("items" in inner || "detail" in inner) return inner;
  }
  if (Array.isArray(root.items)) return root;
  return root;
}

function formatTs(ts?: string) {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

export default function GmailActivity({ portfolio, org }: AgentProps) {
  const [items, setItems] = useState<ActivityEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState("7");
  const [eventType, setEventType] = useState("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<unknown>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const apiBase = import.meta.env.VITE_API_URL;
  const authHeaders = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${sessionStorage.accessToken}`,
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body: Record<string, unknown> = {
        portfolio,
        org,
        days: Number(days) || 7,
        limit: 150,
      };
      if (eventType !== "all") body.event_type = eventType;

      const res = await fetch(`${apiBase}/_schd/${portfolio}/${org}/call/gmail/list_activity`, {
        method: "POST",
        headers: authHeaders,
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError("Could not load activity — run Install to register gmail_activity");
        setItems([]);
        return;
      }
      const unwrapped = unwrapHandlerOutput(data);
      const list = (unwrapped.items as ActivityEntry[]) || [];
      setItems(Array.isArray(list) ? list : []);
    } catch {
      setError("Could not load activity");
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [apiBase, portfolio, org, days, eventType]);

  useEffect(() => {
    void load();
  }, [load]);

  async function loadDetail(entry: ActivityEntry) {
    const id = String(entry.event_id || "");
    if (!id) return;
    if (expandedId === id) {
      setExpandedId(null);
      setDetail(null);
      return;
    }
    setExpandedId(id);
    setDetail(null);
    if (!entry.detail_s3_path) return;

    setDetailLoading(true);
    try {
      const res = await fetch(`${apiBase}/_schd/${portfolio}/${org}/call/gmail/list_activity`, {
        method: "POST",
        headers: authHeaders,
        body: JSON.stringify({
          portfolio,
          org,
          event_id: id,
          detail_s3_path: entry.detail_s3_path,
        }),
      });
      const data = await res.json().catch(() => ({}));
      const unwrapped = unwrapHandlerOutput(data);
      const out = data.output as Record<string, unknown> | undefined;
      setDetail(
        unwrapped.detail ??
          data.detail ??
          (out && typeof out === "object" ? out.detail : null) ??
          null,
      );
    } catch {
      setDetail({ error: "Failed to load detail" });
    } finally {
      setDetailLoading(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-4xl p-4 sm:p-6">
      <Card>
        <CardHeader>
          <CardTitle>Gmail activity</CardTitle>
          <CardDescription>
            Operational trace for this portfolio’s agent inbox — polls, connect/disconnect, and
            email linking. Poll classifications include Gmail message ids so you can look up bodies
            in the mailbox. Large poll batches are stored in S3; expand a row for full detail.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="activity-days">Days</Label>
              <Input
                id="activity-days"
                type="number"
                min={1}
                max={31}
                className="w-24"
                value={days}
                onChange={(e) => setDays(e.target.value)}
              />
            </div>
            <div className="space-y-1.5 min-w-[180px]">
              <Label>Event type</Label>
              <Select value={eventType} onValueChange={setEventType}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {EVENT_TYPES.map((t) => (
                    <SelectItem key={t.value} value={t.value}>
                      {t.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button variant="secondary" onClick={() => void load()} disabled={loading}>
              {loading ? "Loading…" : "Refresh"}
            </Button>
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}
          {!loading && !error && items.length === 0 && (
            <p className="text-sm text-muted-foreground">No activity recorded yet.</p>
          )}

          {items.length > 0 && (
            <div className="overflow-x-auto rounded-md border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
                    <th className="px-3 py-2 font-medium">Time</th>
                    <th className="px-3 py-2 font-medium">Type</th>
                    <th className="px-3 py-2 font-medium">Trigger</th>
                    <th className="px-3 py-2 font-medium">Summary</th>
                    <th className="px-3 py-2 font-medium" />
                  </tr>
                </thead>
                <tbody>
                  {items.map((entry) => {
                    const id = String(entry.event_id || entry.ts || Math.random());
                    const isOpen = expandedId === String(entry.event_id || "");
                    return (
                      <Fragment key={id}>
                        <tr className="border-b last:border-0">
                          <td className="whitespace-nowrap px-3 py-2 align-top text-xs">
                            {formatTs(entry.ts)}
                          </td>
                          <td className="px-3 py-2 align-top font-mono text-xs">
                            {entry.event_type || "—"}
                          </td>
                          <td className="px-3 py-2 align-top text-xs">{entry.trigger || "—"}</td>
                          <td className="px-3 py-2 align-top">{entry.summary || "—"}</td>
                          <td className="px-3 py-2 align-top">
                            {(entry.detail_s3_path || entry.refs) && (
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => void loadDetail(entry)}
                              >
                                {isOpen ? "Hide" : "Detail"}
                              </Button>
                            )}
                          </td>
                        </tr>
                        {isOpen && (
                          <tr className="border-b bg-muted/20">
                            <td colSpan={5} className="px-3 py-3">
                              {entry.refs && Object.keys(entry.refs).length > 0 && (
                                <pre className="mb-2 max-h-40 overflow-auto rounded bg-background p-2 text-xs">
                                  {JSON.stringify(entry.refs, null, 2)}
                                </pre>
                              )}
                              {entry.detail_s3_path && (
                                <>
                                  {detailLoading && (
                                    <p className="text-xs text-muted-foreground">Loading detail…</p>
                                  )}
                                  {!detailLoading && detail != null && (
                                    <pre className="max-h-96 overflow-auto rounded bg-background p-2 text-xs">
                                      {JSON.stringify(detail, null, 2)}
                                    </pre>
                                  )}
                                </>
                              )}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
