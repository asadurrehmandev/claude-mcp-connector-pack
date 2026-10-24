# Threat model

Switchboard sits between an MCP client and a small set of vendor APIs. The thing worth protecting is each client's CRM, ads, docs, tasks, and Slack channel. A token for Halden Bikes must not read Sable & Rye. A sentence stored in a note, a page, or a task must not become an instruction. A write must not land because a model inferred that the user wanted it.

This document describes the controls in this repository and the attacks they do not stop.

## Assets

- Tenant records: contacts, deals, notes, campaigns, daily metrics, knowledge pages, tasks, members, and the Slack allowlist.
- Credentials: vendor tokens in the process environment, the signing secret, the OAuth client secret, and any model key a person pastes into the playground.
- The audit log, which is evidence of what was attempted. It must not become a second copy of those secrets.
- The policy file, which decides which tools a workspace may call.

## Trust boundaries

| Boundary | Who is trusted | What is not |
| --- | --- | --- |
| stdio process | The operating system that started it, and `SWITCHBOARD_TENANT_ID` | Anything the model says |
| HTTP `/mcp` | A bearer token this process can verify | A missing, expired, wrong-audience, or wrong-tenant token |
| Console `/console` in demo mode | Localhost, for the sample UI | It is not an authorization boundary. Do not publish port 4317 in demo mode. |
| Tool results | The envelope's `notice` and `untrusted: true` | The bytes inside `data`, including notes, page bodies, and task descriptions |
| Live vendor HTTP | The mapped account for that tenant | Any account that is not in the tenant map |

The gateway runs the same checks for every tool, whether the caller is the MCP transport, the console playground, or a test: principal, unknown tool, rate limit, workspace policy, scope, `workspace_id` match, then the handler.

## Tokens

Three access-token shapes are accepted.

- `sb1.<payload>.<hmac>` — HMAC-SHA256 over the payload with the signing secret. Used by tests and by the short-lived demo bearer the console shows.
- HS256 JWT (`eyJ…`) signed with the same secret.
- RS256 JWT signed by this process. The public key is `GET /.well-known/jwks.json`. The issuer mints these for `client_credentials`, authorization-code + PKCE S256, and refresh.

Claims that are checked: `aud` equals `{public_base}/mcp` (a string or a list that contains that resource), `exp` is strictly in the future (`exp <= now` is rejected), and `nbf`, when present, is not in the future. `alg: none` is rejected. An RS256 `kid` must match this process's key.

Access tokens last `ACCESS_TTL` seconds (3600). Refresh tokens are opaque `sbr.` values, stored in memory, and last 14 days. A refresh is single-use: the old value is removed before the new pair is issued, so a replay returns `invalid_grant`. The refresh record stores the audience; a mismatch returns `invalid_target`.

The demo signing secret is `switchboard-demo-signing-secret`. Live mode (`SWITCHBOARD_ENV=live`) refuses to start unless `SWITCHBOARD_SIGNING_SECRET` is at least 32 characters and is not that demo value. Set `SWITCHBOARD_JWT_PRIVATE_KEY_PEM` if RS256 tokens should survive a restart. Without it, the RSA key is generated once per process and dies with the process, which also invalidates outstanding RS256 access tokens. Refresh tokens are in-memory either way.

The built-in issuer has one pre-registered client. Demo mode auto-approves the authorization code. There is no end-user password. A `redirect_uri` that is not registered returns 400 JSON and is not followed.

## Tenancy

One token carries one `tenant_id`. Passing `workspace_id` for a different client returns `tenant_isolation` and does not read that client's store. Direct id lookups for another tenant's contact, deal, page, task, or campaign return `not_found`. Search results are filtered by tenant before they leave the handler. Prompts registered on the server do not embed another client's records.

Live connectors refuse to run when the tenant map is missing (`not_configured`) rather than calling a default account. HubSpot queries include the `switchboard_tenant` property. Google, Meta, Notion, ClickUp, and Slack use the workspace's mapped customer, account, page ids, list, or channel allowlist.

A workspace that is not one of the three demo tenants is read-only until its policy enables a write tool. Enabling the tool still requires `confirm: true`.

## Writes

Write tools (`crm_upsert_contact`, `crm_upsert_deal`, `crm_log_note`, `projects_create_task`, `projects_update_task`, `slack_notify`) return a dry-run preview when `confirm` is false or omitted. The store revision does not move. If a handler mutates the store and still reports a dry-run, the gateway restores the snapshot and records `unsafe_write_blocked`.

`projects_update_task` is marked destructive. Slack posts only to channels on that workspace's allowlist; another channel is rejected even when `confirm` is true. ClickUp create checks that the assignee is already a member.

## Untrusted results

Every tool result is `{untrusted, notice, dry_run, data}`. The notice is a fixed string. It does not echo the payload. Tool descriptions end with the same hardening sentence. The seed includes instruction-shaped text in a CRM note, a Notion page (including a markdown link), a ClickUp task description, and a zero-width obfuscation of "ignore previous instructions". Those strings stay inside `data`.

The scripted planner normalizes NFKC and strips zero-width characters before it routes, and it refuses a turn that asks to ignore prior instructions, print a secret, or post a key, token, or password to Slack. That refusal is a property of this planner. A hosted model on the bring-your-own-key path is told the same thing in the tool descriptions and the result notice. This repository does not claim the hosted model will obey it. That path is not part of the sealed eval.

## Logging and redaction

Audit arguments pass through a redactor before they are stored. Bearer values, `sb1` tokens, `sk-` keys, email local-parts, phone numbers, and SSNs are masked. Note bodies are not copied into the audit summary. The same redactor is attached to Python log records. A model API key is not a field on the audit row. If a provider echoes the key, the reply text has that string removed before it is shown.

Rate limiting is a sliding window per tenant and tool. The default is 60 calls per 60 seconds. The error includes `retry_after_seconds`. Another tenant's window is separate.

## What this does not stop

- A person who holds a valid, unexpired token for a tenant can call every tool that token's scopes and the workspace policy allow. Theft of that token is theft of that tenant for the token's remaining life.
- The console API in demo mode does not check a bearer token. Anyone who can reach port 4317 can call `/console`. MCP still checks the bearer.
- Refresh tokens and the generated RSA key do not survive a process restart. There is one JWKS key, not a rotation set.
- There is no user login. Demo OAuth auto-approves the pre-registered client.
- The sealed eval grades the scripted planner, not Claude or another hosted model. A model can still be persuaded by text inside `data`. The server's job is to keep that text out of the control channel and to refuse the write unless `confirm` is true.
- Live vendor clients are real HTTP code. They were tested with a mock transport, not with production agency accounts. Those accounts do not exist; Fernwood Studio, Halden Bikes, Sable & Rye, and Brightline Pediatric Dentistry are fictional records written for this project.
- Anomaly thresholds are fixed. They are not a client's media goal.
- ClickUp's live update path changes name and status. Reassignment is guaranteed on create, where the member id is sent.
- Policy and audit files under `var/` are local JSON, not a tamper-evident log pipeline.
