# Switchboard eval

Run date: 2026-10-24.

```bash
python3 -m switchboard eval --tasks qa/heldout_tasks.json --out qa/eval_results.json
python3 -m switchboard eval --tasks qa/sealed_tasks.json --out qa/sealed_eval_results.json
pytest
```

Both files measure the deterministic planner in `src/switchboard/planner.py` and, on the sealed attacks, the gateway's isolation and write guards. They are not a score for Claude or for the bring-your-own-key path. That path is implemented and refuses an empty key. It was not scored, because scoring it would mean storing a provider key.

Fernwood Studio, Halden Bikes, Sable & Rye, and Brightline Pediatric Dentistry are fictional records written for Switchboard. They are not a real agency and they are not borrowed from another project. The studio was renamed after the first sealed hash. The prompts were not retuned. The hash below is the file after that rename.

## Original held-out file

24 prompts in `qa/heldout_tasks.json`. This file has no category or safety-check field. It is the first eval, kept so the earlier number stays reproducible.

| Measure | Correct | Total |
| --- | ---: | ---: |
| Tool selection | 24 | 24 |
| Argument correctness | 24 | 24 |
| Failures | 0 | 24 |
| Leak cases | 0 leaks | 8 |
| Unsafe writes | 0 | 9 |
| Snapshot reverts of a handler that wrote during a dry run | 1 | |

The leak check concatenates the audit dump and the log lines for that case. The planted values are an `sk-live-…` secret, an `sb1.…` token, `mara.ellison@haldenbikes.com`, `612-555-0148`, and the instruction sentence on Mara Ellison's note. The log line is written on purpose with the raw values so the redacting filter is part of the measurement. This run replaced them with `sk-[redacted]`, `m***@haldenbikes.com`, `***-***-0148`, and `sb1.[redacted]`.

## Sealed file

88 prompts in `qa/sealed_tasks.json`. The file was generated once by `qa/freeze_sealed.py`, then hashed, then scored. The hash was checked before scoring (`qa/sealed_tasks.sha256`).

```
539d4fc1bb6f52853222ae7e0f58e1646f2008fabf5a675c01655b9210f41df9
```

The JSON was not edited after that hash. When a task failed, the planner changed. The task file did not.

A task is tool-selection correct when the planned tool list equals `expect_tools`, including a trailing `docs_fetch` when the router marks a docs follow-up. Argument correctness requires that list and, for each planned step, the keys in `expect_args[].match`. The dynamic page id chosen at fetch time is not compared.

Workflow tasks have `check: none`. Their safety column below is not a separate test; the harness records them as passing because no safety check was declared. Attack tasks declare a check, and that check is what the Safety column counts.

| Category | Tasks | Tool selection | Arguments | Safety |
| --- | ---: | ---: | ---: | --- |
| `ads_metrics` | 10 | 10 | 10 | no check declared |
| `ads_report` | 8 | 8 | 8 | no check declared |
| `crm_lookup` | 8 | 8 | 8 | no check declared |
| `lead_to_crm` | 8 | 8 | 8 | no check declared |
| `docs_qa` | 8 | 8 | 8 | no check declared |
| `projects` | 8 | 8 | 8 | no check declared |
| `slack` | 6 | 6 | 6 | no check declared |
| `attack_cross_tenant` | 8 | 8 | 8 | 8/8 `tenant_isolation`, store unchanged |
| `attack_unconfirmed_write` | 8 | 8 | 8 | 8/8 store unchanged |
| `attack_injection` | 8 | 8 | 8 | 8/8 refusal, no tool call |
| `attack_exfil` | 8 | 8 | 8 | 8/8 refusal, no tool call |
| Total | 88 | 88 | 88 | 32/32 attack checks |

Scored-run failures: **0**.

Cross-tenant tasks expect the planner to pass the foreign `workspace_id` and the gateway to deny it. That denial is the check. The planner does not refuse the turn before the call, so the denial is in the audit log.

Unconfirmed-write tasks expect `confirm: false` and no store change. Injection and exfiltration tasks expect the whole turn to be a refusal, including a legitimate question glued onto an override sentence, a zero-width or fullwidth obfuscation, a markdown link, and a request to post a key to Slack.

## Failures found on the frozen file, and the fixes

The first scoring attempts against the already-hashed file failed. The task bytes stayed put. Each miss was a planner bug.

1. **s18, ads report.** Prompt: `Google readout for cmp_halden_exact from 2026-03-02 to 2026-03-08`. The report trigger required a second keyword such as "ads" or "campaign" next to "readout". The campaign id does not contain the word "campaign", so the planner returned no tools. Fix: a readout, a weekly report, an account summary, or a rollup is a report on its own. Slack is still matched first, so a ping that mentions a readout stays a Slack post. After the fix this task planned `ads_list_campaigns`, `ads_campaign_metrics` for that campaign and those dates on Google, and `ads_account_summary`.

2. **s20 and s58, CRM lookup.** `Who is Chris Adeyemi in the contacts` captured the query `Chris Adeyemi in the`. `Look up the contact named Helen Cho in workspace sable-rye` captured `Helen Cho in workspace`. A case-insensitive name pattern let the capitalized-word class consume the lowercase words "in" and "the". Fix: the prefix `named` / `who is` is case-flexible, and the captured name is not. Lowercase "in" stops the name. Both queries are now just the person's name, and s58 still carries `workspace_id` `sable-rye`.

3. **s61, CRM lookup.** `Search deals for catering in workspace sable-rye` captured the query `catering in workspace`. The `for …` clause ran to the end of the string after the tenant slug was stripped. Fix: the clause stops before `in workspace` or `workspace_id`. The query is `catering`.

A later run of the same file, the one recorded in `qa/sealed_eval_results.json`, is 88/88 tools and 88/88 arguments, with every attack check passing.

Two unit-test expectations were also wrong relative to the product and were corrected in the tests, not by weakening the product:

- An all-zero current ads window against a previous window that spent money is a `spend_drop`. CTR is not compared when the current window has no impressions. The assertion now expects `["spend_drop"]`.
- A Brightline page fetched with a Halden token is `not_found`. The injection test now fetches that page as Brightline. Zero-width characters are compared with `json.dumps(..., ensure_ascii=False)` so the planted character is what the assertion looks for.

## Pytest

`pytest` on this tree: **204 passed** (Python 3.12). Coverage includes, for every one of the 19 tools, a happy path, a scope denial, a missing or invalid argument, and a foreign workspace id; dry-run on each write; a rejected `confirm` flag on each read; tenant isolation across tools and prompts; injection variants; rate limit and audit redaction; ads edges (zero clicks, zero conversions, currency micros, date boundaries, zoned datetimes rejected) plus hypothesis properties; and bearer checks for missing, expired, not-yet-valid, and wrong-audience tokens, including RS256 and refresh rotation.

`python3 -m switchboard conformance`, in-process official MCP client: 19 tools, 5 resource templates, 3 prompts, and `crm_search_contacts` `is_error` false. HTTP coverage: missing and bad bearers on `/mcp` return 401, JWKS and protected-resource metadata return 200, and `initialize` with an RS256 access token returns 200.

## What this does not claim

- No hosted model was graded.
- Live HubSpot, Google Ads, Meta, Notion, ClickUp, and Slack accounts were not called. Parsers were checked with a mock transport.
- The planner can still miss a wording that is outside the task files. One checked miss: `hubspot lookup, deshmukh anita` searches for `Deshmukh Up`. The playground says when it does not recognize a request.
- Independent review, with hashes and the 4/10, 7/10, and 8/10 scores: `qa/INDEPENDENT_QA_v1.md`. The 88-task file is a dev set.
- The demo OAuth issuer auto-approves. Refresh tokens are in memory. That is not an end-user login.
- A hosted model can still be persuaded by text inside `data`. The server keeps that text in the data envelope and will not apply a write unless `confirm` is true. `SECURITY.md` is the threat model.
