import ChatInspect from "@extensions/data/ui/pages/chat_inspect";
import { getCurrentUserId } from "@extensions/data/ui/utils/current-user-id";

interface AgentProps {
  portfolio: string;
  org: string;
  tool: string;
}

/** Portfolio-scoped Gmail sessions live at org ``_all``. */
const SESSION_ORG = "_all";

export default function GmailConversations({ portfolio, tool }: AgentProps) {
  const userId = getCurrentUserId();

  if (!userId) {
    return (
      <div className="mx-auto max-w-2xl p-6 text-sm text-muted-foreground">
        Could not resolve your user id — sign in again or reload the console home page.
      </div>
    );
  }

  return (
    <ChatInspect
      portfolio={portfolio}
      org={SESSION_ORG}
      tool={tool}
      readOnly
      title="Email threads"
      description={`Threads for user-gmailthread (entity_id prefix ${userId}-…). Each Gmail email thread is its own entity_id; create a new thread after compaction to reset context.`}
      fixedEntityType="user-gmailthread"
      fixedEntityId={`${userId}-`}
      threadSource="query_prefix"
      apiSegment="_session"
    />
  );
}
