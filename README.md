# dataverse-support-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an AI agent (built and tested for
[Hermes Agent](https://hermes-agent.nousresearch.com), works with any MCP client) act as a first-line
support agent on top of **Microsoft Dynamics 365 / Dataverse**:

```
customer email → support mailbox (Exchange Online) → Dynamics 365 server-side sync
   → Email activity in Dataverse → this MCP server → agent reads, looks up the knowledge base,
   → drafts (or sends) a reply, or escalates to a human as a Case
```

Why Dataverse instead of IMAP/SMTP? Microsoft retired password / app-password logins for
Outlook.com and Exchange Online IMAP/SMTP, so generic email gateways can no longer connect to those
mailboxes. Dynamics 365 already syncs the mailbox with modern auth, and this server works on the
resulting records using an Entra ID app registration (OAuth client credentials).

> **Status: 0.1.0.** The logic is covered by unit tests against a mocked Dataverse API, and the
> server starts and registers its tools over stdio. It has **not** been run against a live
> Dataverse org yet — run `dataverse-support-mcp --check` and try it in a sandbox first. A few
> Dataverse field and navigation-property names can differ per org; see [Known limits](#known-limits).

## Tools

| Tool | What it does |
|---|---|
| `list_new_emails` | Unprocessed incoming emails (metadata only, oldest first). No-reply/bounce senders and the support mailbox itself are skipped and marked processed automatically. |
| `get_email` | Plain-text body, sender, matching contact, last emails on the same case. Body is flagged as untrusted. |
| `find_contact` / `create_contact` | Look up or create the sender's contact (idempotent). |
| `search_knowledge` | Keyword search over **published** Knowledge Articles, ranked, returned as plain text. |
| `create_reply` | Creates a reply (HTML-escaped, optional signature). **Draft by default.** |
| `escalate_to_human` | Creates a Case (origin Email), links the email, optionally assigns to a team, marks processed. |
| `mark_processed` | Close out mail that needs no answer. |

## Quick start

### 1. Dataverse

1. **Entra ID → App registrations**: create an app, add a client secret. Note tenant id, client id, secret.
2. **Power Platform admin center → your environment → Settings → Users + permissions → Application users**:
   add the app as an application user.
3. Give it a **minimal custom security role**: Email (create, read, write, append, append to), Contact
   (create, read, append to), Case (create, read, write, append, append to, assign), Knowledge Article
   (read), Queue (read, append to), Team and User (read). Do not use System Administrator.
4. Add a **Yes/No column `ts_hermes_processed`** on the **Email** table (or choose another name and set
   `DV_PROCESSED_FIELD`). It prevents the same email from being handled twice.
5. Make sure the support mailbox (or queue mailbox) is **approved, enabled for server-side sync and
   passes the incoming/outgoing email test**.

### 2. Check the setup

```bash
export DV_URL=https://yourorg.crm5.dynamics.com
export DV_TENANT_ID=... DV_CLIENT_ID=... DV_CLIENT_SECRET=...
uvx dataverse-support-mcp --check
```

Prints one line per check (auth, the processed column, read access, current mode).

### 3. Add it to Hermes

`~/.hermes/config.yaml`:

```yaml
mcp_servers:
  dv_support:
    command: "uvx"
    args: ["dataverse-support-mcp"]
    # or straight from git:
    # args: ["--from", "git+https://github.com/<you>/dataverse-support-mcp", "dataverse-support-mcp"]
    env:
      DV_URL: "https://yourorg.crm5.dynamics.com"
      DV_TENANT_ID: "..."
      DV_CLIENT_ID: "..."
      DV_CLIENT_SECRET: "..."
      DV_SUPPORT_ADDRESS: "support@yourdomain.com"
      DV_SENDER_QUEUE_ID: "<guid of the support queue>"
      DV_REPLY_SIGNATURE: "Best regards,\\nSupport Team"
    tools:
      include: [list_new_emails, get_email, find_contact, create_contact, search_knowledge, create_reply, escalate_to_human, mark_processed]
```

Then run `/reload-mcp` in Hermes. Tools appear as `mcp_dv_support_<tool>`. Hermes only passes the `env`
values you list to the subprocess.

### 4. Give the agent its job

Copy [`examples/SOUL.md`](examples/SOUL.md) to `~/.hermes/SOUL.md` and adapt it (tone, language,
escalation rules). Then schedule the loop, for example by telling Hermes:

> Every 5 minutes, call list_new_emails. For each email: read it with get_email, search_knowledge for
> the problem, then create_reply if the knowledge base answers it, otherwise escalate_to_human.

### 5. Go live gradually

By default **nothing is sent**: `create_reply` saves drafts that a person reviews in Dynamics. When the
drafts are consistently good, set `DV_ALLOW_SEND=true`. Only then does `send=true` actually send.

## Configuration

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `DV_URL` | yes | | Environment URL, `https://<org>.crm*.dynamics.com` |
| `DV_TENANT_ID` / `DV_CLIENT_ID` / `DV_CLIENT_SECRET` | yes | | Entra ID app registration |
| `DV_SENDER_QUEUE_ID` or `DV_SENDER_USER_ID` | recommended | derived from the original email | Mailbox the reply is sent from |
| `DV_SUPPORT_ADDRESS` | recommended | | Only emails to this address are handled; replying to it is blocked (loop guard) |
| `DV_ALLOW_SEND` | no | `false` | Allow `create_reply(send=true)` to really send |
| `DV_PROCESSED_FIELD` | no | `ts_hermes_processed` | Logical name of the Yes/No column on Email |
| `DV_REPLY_SIGNATURE` | no | | Appended to replies (`\n` = new line) |
| `DV_BLOCKED_SENDERS` | no | | Comma-separated addresses or `@domains` never auto-answered |
| `DV_MAX_BODY_CHARS` | no | `8000` | Max email body characters returned to the model |
| `DV_API_VERSION` | no | `v9.2` | Web API version |
| `DV_LOG_LEVEL` | no | `INFO` | Logs go to stderr |

## Safety model

Incoming email is **untrusted input** and can contain prompt injection. The server limits the damage:

- Outbound text is plain text from the model, **HTML-escaped**; the model cannot inject links or markup.
- **Draft-only by default**; sending requires an operator-set environment variable the model cannot change.
- No replies to no-reply / bounce / own-mailbox / blocklisted addresses; no second reply to an
  already-processed email; replies are only created for *incoming* emails.
- Ids from the model are validated as GUIDs before use, and OData string values are escaped.
- The marker is set **before** sending, so a failure never produces duplicate replies.
- Updates use `If-Match: *`, so a wrong id can never create a new row.
- The application user's security role is your real boundary: keep it minimal.

Also disable Hermes' terminal/file toolsets for the support agent and run it in its own container or VM.

## Known limits

- Tested with mocked HTTP only. Verify against a sandbox org before production.
- `statuscode eq 4` (Received) and `statecode eq 3` (Published knowledge article) are the standard values.
- Linking the reply to the original email (`parentactivityid_email`) and to the Case
  (`regardingobjectid_<entity>_email`) is best effort: if your org rejects those navigation properties,
  the reply is created without links (`linked_to_original: false`) and processing continues.
- Knowledge search uses simple `contains` filters on Knowledge Articles. For SharePoint or a custom
  knowledge source, adapt `search_knowledge` in `service.py`.
- Public-cloud authority only (`login.microsoftonline.com`).
- Attachments are not read.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest
```

Publishing: create the project on PyPI, add this repository as a trusted publisher, then publish a GitHub
release; `.github/workflows/publish.yml` builds and uploads. Check that the name `dataverse-support-mcp`
is free on PyPI, or rename it in `pyproject.toml`.

## License

MIT
