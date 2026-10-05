# Gmail extension — specs

## Provider

Gmail API v1 via OAuth 2.0 (authorization code + offline refresh). No Pub/Sub, IMAP, or domain-wide delegation in v1.

## OAuth (per portfolio)

- Each portfolio stores `oauth_client_id`, `oauth_client_secret`, and `oauth_state_secret` on singleton `gmail_config` at `_all`. There is no platform OAuth client or platform state secret.
- Connect: Google consent as `agent@any-domain.com` → refresh tokens stored on that same document.
- Callback: `GET /_schd/gmail/oauth_callback` (no Cognito). Signed `state` carries the portfolio and the absolute console `return_url` of the browser that was already logged in.
- Scopes: `openid`, userinfo email/profile, `gmail.modify`, `gmail.send`.
- Callback sent to Google is `gmail_config.oauth_redirect_uri` when set, otherwise `{BASE_URL}/_schd/gmail/oauth_callback`. The Google client must list that exact URI.

## Agent mailbox

One connected inbox per **portfolio** (singleton at `_all`), shared by all orgs in that portfolio. Staff can open that mailbox in Gmail for accountability; all agent replies appear in the same threads.

## Identity

User-linked via dashboard-minted `LINK-<20>` tokens (same semantics as WhatsApp), stored at portfolio `_all`:

- SHA-256 at rest, 10 minute TTL, single-use
- No silent auto-link
- No-steal if email already bound to another user
- Replace-on-link for the same user
- Transport: user emails the agent inbox with the code in subject or body

## Ingress

Polling (`gmail/poll_inbox`), typically `rate(2 minutes)` via EventBridge → `/_schd/ingress` (`type: schd_job`; `/_schd/ping` remains a compat alias), or `POST /_schd/ingress` with `channel=gmail-poll`. Deprecated: `POST /_schd/process-gmail-poll`. Manual **Poll now** from Settings.

Inbox query: `in:inbox is:unread`. While a pending LINK code exists, spam is also scanned with `in:spam is:unread "LINK-"` (max 10 fetched, 5 processed per poll). Only messages whose extracted code matches a pending row are handled; invalid spam LINK mail is marked read with no agent/reply. Successful links are moved from Spam to Inbox.

## Agent routing

After link, messages go to `gmail_config.agent_handler` (default `dumbo/generic_agent`). Replies use Gmail `threadId` plus RFC822 `In-Reply-To` / `References`.

## Session coordinates

Each Gmail email-thread maps to its own Renglo coordinates (org `_all`):

| Field | Value |
|-------|--------|
| `entity_type` | `user-gmailthread` |
| `entity_id` | `{renglo_user_id}-{gmail_threadId}` |
| `thread` | Renglo thread UUID from `create_thread` / `ensure_latest_thread` |

Inbound mail calls `ensure_latest_thread` so turns always attach to a registered Renglo thread (newest by time). Create another Renglo thread under the same coordinates after memorialization/compaction to reset context. Console **Gmail → Threads** lists those Renglo thread documents (not Gmail API ids as fake threads).

## Activity log

Hybrid storage (see README): daily index in `gmail_activity`, poll detail in S3. Settings `poll_batch_size` controls unread fetch limit per poll.
