# Gmail Agent Extension (Renglo)

Portfolio-wide **agent inbox channel** (config at `_all`, same balance as WhatsApp): connect one Gmail mailbox (any domain, e.g. `agent@acme.com`) for the whole portfolio, let users **LINK** their personal email, then treat inbound mail from linked senders as chat input and reply **in the same thread**.

**Tenant UX matches Asana / cos-demo:** click **Connect agent inbox** → Google consent popup → Approve. Tenants never create a GCP project or paste OAuth client secrets.

---

## What this is (and is not)


|              | This extension                                       | Old `example/gmail`       | cos-demo “Link Google”       |
| ------------ | ---------------------------------------------------- | ------------------------- | ---------------------------- |
| Purpose      | Agent **channel** (inbound → agent → threaded reply) | Same idea, heavy GCP push | Personal Workspace **tools** |
| OAuth client | **Platform*** owns one Google app                    | Per-deploy GCP + Pub/Sub  | Platform owns one Google app |
| Tenant setup | Connect consent only                                 | Watch + DWD + tokens      | Link Google                  |
| Who can talk | **Linked** senders only (`LINK-…`)                   | Open / FAQ-style          | N/A                          |


**Accountability:** the extension acts as the agent mailbox via Gmail API, so every inbound message and reply appears in that inbox in the normal Gmail UI.

**Not required:** Pub/Sub, domain-wide delegation, service accounts, org-policy exceptions, watch renewal, or per-tenant GCP projects.

---



## Platform setup (once)

Done by the Renglo operator — not by each portfolio/org.

### 1. Create a GCP project + enable Gmail API

1. [Google Cloud Console](https://console.cloud.google.com/) → create/select a project owned by the **platform**
2. **APIs & Services → Library** → enable **Gmail API**
3. Do **not** enable Pub/Sub for this extension



### 2. OAuth consent screen

1.  **Google Auth Platform > Branding**
2. For agent inboxes on **any Google domain**, choose **External**
3. App name: e.g. `Renglo Gmail`
4. Add scopes:
  - `openid`
  - `.../auth/userinfo.email`
  - `.../auth/userinfo.profile`
  - `https://www.googleapis.com/auth/gmail.modify`
  - `https://www.googleapis.com/auth/gmail.send`
5. While unverified, add **test users** (Google accounts that will Connect agent mailboxes), or complete Google’s verification for production

> `gmail.modify` / `gmail.send` are restricted scopes. External apps need verification for unrestricted production use — same class of work products like Asana complete once. Until then, Testing mode + test users is enough to develop.



### 3. Create OAuth client (Web application)

1. **Google Auth Platform > Clients >Create client** 
2. Type: **Web application**
3. **Authorized redirect URIs** — exact value:

```text
{BASE_URL}/_schd/gmail/oauth_callback
```

Examples: `https://api.your-host.com/_schd/gmail/oauth_callback` or local API URL.

1. Copy **Client ID** and **Client secret**



### 4. Configure the Renglo API

In `env_config.py` / environment (see `dev/renglo-api/env_config.py.TEMPLATE`):

```text
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
BASE_URL=https://api.your-host.com
# optional:
# GMAIL_OAUTH_REDIRECT_URI=https://api.your-host.com/_schd/gmail/oauth_callback
# OAUTH_STATE_SECRET=...   # else AUTH_SECRET / SECRET_KEY
```

Also ensure state signing has `OAUTH_STATE_SECRET`, `AUTH_SECRET`, or `SECRET_KEY`.

---



## Tenant setup (each portfolio)

1. Install the extension (Marketplace → Gmail → select portfolio)
2. From any org in that portfolio, open **Gmail → Settings**
3. Click **Connect agent inbox**
4. Sign in to Google as the agent mailbox (`agent@acme.com` or any other domain you can access) and **Allow**
5. Console shows **Connected ✓** — all orgs in the portfolio share that inbox

No GCP, no client secrets, no redirect URI registration for the tenant.

### User linking (LINK)

1. User opens **Gmail → Connect email** → mint `LINK-…`
2. Email that code to the agent inbox (subject or body)
3. Poller binds `From` → Renglo user; later mail from that address reaches the agent

---



## Renglo install (ops)

```bash
# Blueprints
cd extensions/gmail/installer
python upload_blueprints.py <env> --aws-profile <profile> --aws-region <region>

# Python package (API environment)
pip install -e extensions/gmail/package
```

Console: include `gmail` in `VITE_EXTENSIONS`.

Onboarding creates the tool, `schd_tools`, singleton `gmail_config`, poll job, and best-effort `rate(2 minutes)` EventBridge rule targeting `POST /_schd/ingress` (`type: schd_job`). If cron fails, use **Poll now** or `POST /_schd/ingress` with `{"type":"webhook","channel":"gmail-poll","portfolio":"...","org":"..."}` (header `X-Renglo-Ingress-Secret` when `RENGLO_INGRESS_SECRET` is set). Deprecated alias: `POST /_schd/process-gmail-poll`.

---



## Operations


| Topic       | Behavior                                                                                                      |
| ----------- | ------------------------------------------------------------------------------------------------------------- |
| Poll        | ~2 min cron and/or **Poll now** (`trigger`: cron vs manual)                                                   |
| Batch       | `gmail_config.poll_batch_size` (1–100, default 25)                                                            |
| Unread      | Process `in:inbox is:unread`, then clear `UNREAD`                                                             |
| Spam LINK   | While a LINK code is pending, also scan spam for `"LINK-"` messages matching that code (capped; see Security) |
| Activity    | **Gmail → Activity** — indexed log + S3 poll detail                                                           |
| Tokens      | Refresh via platform OAuth client + stored refresh token                                                      |
| Agent       | `gmail_config.agent_handler` (default `dumbo/generic_agent`)                                                  |
| Gmail reply | `threadId` + `In-Reply-To` / `References`                                                                     |
| Sessions    | `user-gmailthread` / `{userId}-{gmailThreadId}` + Renglo thread UUID (`ensure_latest_thread`)                 |
| Threads UI  | **Gmail → Threads** — Renglo threads for your user (read-only)                                                |




### Common Connect failures

- **Platform credentials missing:** set `GOOGLE_OAUTH_`* on the API
- **redirect_uri_mismatch:** platform OAuth client redirect must match `BASE_URL` / `GMAIL_OAUTH_REDIRECT_URI`
- **Access blocked / not a test user:** add the agent Google account as a test user, or finish verification

---



## Security

- Platform holds the OAuth client secret; the portfolio holds mailbox tokens on `gmail_config` at `_all`
- Anyone who can open the agent mailbox in Gmail can audit the same threads (intentional)
- Linked-only: unlinked senders are ignored (marked read, no outbound mail). Agent replies only to linked senders. A single confirmation is sent when email linking succeeds via LINK code.
- **Spam LINK hardening:** Spam is scanned only while an unconsumed LINK code is pending (10‑minute window). Query is narrow (`"LINK-"` in spam). At most 10 fetched / 5 processed per poll; messages matching a valid pending code are prioritized; after 3 invalid spam LINK attempts in one poll, the rest are skipped. Invalid spam LINK mail is marked read (no agent, no reply). Successful links are moved from Spam to Inbox.
- OAuth `state` is HMAC-signed

---



## You do NOT need (tenants or platform for v1 mail)

- Per-tenant GCP projects  
- Cloud Pub/Sub / Gmail watch / DWD  
- Pasting client id/secret in the org Settings UI

Optional advanced override: empty `oauth_client_*` fields on `gmail_config` can override the platform client if both are set (not shown in the default Settings UI).

---



## Activity traceability

Operational events are recorded in ring `gmail_activity` (one document per UTC day, append-only `entries[]`). Poll batches store full message classifications in S3 (`_files/{portfolio}/_all/gmail_activity/{date}/{event_id}.json`); entries reference Gmail `msg_id` / `thread_id` (no email bodies stored).


| Event type             | When                                      |
| ---------------------- | ----------------------------------------- |
| `mailbox_connected`    | OAuth callback succeeded                  |
| `mailbox_disconnected` | Settings disconnect                       |
| `poll`                 | Cron or manual poll completed             |
| `poll_skipped`         | Poll not ready (disabled / not connected) |
| `poll_error`           | Gmail list failed                         |
| `identity_linked`      | Inbound LINK code consumed                |
| `identity_unlinked`    | User unlinked in console                  |


Console: **Gmail → Activity**. API: `POST …/call/gmail/list_activity` with `{ days, limit, event_type? }` or `{ event_id }` for S3 detail.

Upload blueprint `gmail_activity` on deploy (`upload_blueprints.py`).

---

