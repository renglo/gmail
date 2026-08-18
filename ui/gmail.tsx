import { useEffect } from "react";
import GmailChannels from "@extensions/gmail/ui/pages/channels";
import GmailSettings from "@extensions/gmail/ui/pages/settings";
import GmailActivity from "@extensions/gmail/ui/pages/activity";
import GmailConversations from "@extensions/gmail/ui/pages/conversations";

interface Portfolio {
  name: string;
  portfolio_id: string;
  orgs: Record<string, Org>;
  tools: Record<string, Tool>;
}

interface Org {
  name: string;
  org_id: string;
  tools: string[];
}

interface Tool {
  name: string;
  handle: string;
}

export default function Gmail({
  portfolio,
  org,
  tool,
  section,
  tree,
  onNavigate,
}: {
  portfolio: string;
  org: string;
  tool: string;
  section?: string;
  tree?: { portfolios: Record<string, Portfolio> };
  onNavigate?: (path: string) => void;
}) {
  useEffect(() => {
    if (!section && onNavigate) {
      onNavigate(`/${portfolio}/${org}/${tool}/channels`);
    }
  }, [section, portfolio, org, tool, onNavigate]);

  if (!section) {
    return null;
  }

  return (
    <div className="flex min-h-screen w-full flex-col bg-muted/40">
      <div className="flex flex-col sm:gap-2 sm:pl-2">
        {section === "channels" && (
          <GmailChannels
            portfolio={portfolio}
            org={org}
            tool={tool}
            tree={tree}
            onNavigate={onNavigate ?? (() => {})}
          />
        )}
        {section === "settings" && (
          <GmailSettings
            portfolio={portfolio}
            org={org}
            tool={tool}
            tree={tree}
            onNavigate={onNavigate ?? (() => {})}
          />
        )}
        {section === "activity" && (
          <GmailActivity
            portfolio={portfolio}
            org={org}
            tool={tool}
            tree={tree}
            onNavigate={onNavigate ?? (() => {})}
          />
        )}
        {section === "conversations" && (
          <GmailConversations portfolio={portfolio} org={org} tool={tool} />
        )}
      </div>
    </div>
  );
}
