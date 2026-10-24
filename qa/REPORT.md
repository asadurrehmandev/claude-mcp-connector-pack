# Switchboard eval

Run date: 2026-10-24. Command:

```bash
python3 -m switchboard eval --tasks qa/heldout_tasks.json --out qa/eval_results.json
```

The raw file is `qa/eval_results.json`. These numbers are from that run. They measure the deterministic planner in `src/switchboard/planner.py` and the gateway's leak and write guards. They are not a score for Claude or for the bring-your-own-key path. That path is implemented and refuses an empty key, and it was not scored because scoring it would mean storing a provider key.

## Tool selection and arguments

24 held-out prompts in `qa/heldout_tasks.json`. A task counts as tool-selection correct when the planned tool list equals `expect_tools`, including a trailing `docs_fetch` when the router marks a docs follow-up. Argument correctness requires the same tool list and, for each planned step, the keys in `expect_args[].match`. The dynamic page id chosen at fetch time is not compared.

| Measure | Correct | Total | Rate |
| --- | ---: | ---: | ---: |
| Tool selection | 24 | 24 | 1.0 |
| Argument correctness | 24 | 24 | 1.0 |
| Failures | 0 | 24 | |

The prompts cover campaign metrics (including the exact-math window for `cmp_halden_exact`), a weekly account readout, contact and deal search, a lead dry-run, docs search plus fetch, task list and update, members, Slack on an allowlisted channel and on `#general`, a refusal, a workspace mismatch, a one-day Google spend question, a note, and a prompt the router does not understand.

## Leaks

8 cases. After each call, the check concatenates the full audit dump and the log lines emitted for that case. A leak is a remaining copy of any of: the planted `sk-live-…` secret, the planted `sb1.…` token, `mara.ellison@haldenbikes.com`, `612-555-0148`, or the instruction sentence stored on Mara Ellison's note.

The log line is written on purpose with those raw values so the redacting log filter is part of the measurement. The filter's output in this run replaced them with `sk-[redacted]`, `m***@haldenbikes.com`, `***-***-0148`, and `sb1.[redacted]`.

| | |
| --- | ---: |
| Cases | 8 |
| Leaks | 0 |

## Unsafe writes

8 write calls with `confirm` false or omitted: contact, deal, note, task create, task update, Slack, and a second deal. A ninth case registers a handler that saves a contact and then returns `dry_run: true`. The gateway must restore the pre-call snapshot. A case counts as an unsafe write if the store revision, contact count, or Slack message count changes.

| | |
| --- | ---: |
| Cases | 9 |
| Unsafe writes | 0 |
| Snapshot reverts recorded by the gateway | 1 |

The single revert is the ninth case. The handler did mutate the store; the gateway rolled it back, so the revision matched the pre-call value and the case was not counted as a write.

## Pytest and MCP handshake

`pytest` on this tree: **19 passed** in under a second (Python 3.12). That includes the eval assertions (24/24, leak count 0, unsafe-write count 0), the hand-calculated ads fixture, scope denial, rate limit, confirm, tenant isolation, and injection-in-results.

`python3 -m switchboard conformance`, in-process official MCP client:

| | |
| --- | ---: |
| Tools | 19 |
| Resource templates | 5 |
| Prompts | 3 |
| `crm_search_contacts` is_error | false |

HTTP coverage in `tests/test_live_and_protocol.py`: missing and bad bearer tokens on `/mcp` return 401, protected-resource metadata returns 200, the demo client-credentials token returns 200, and `initialize` with that bearer returns 200.

## What this does not claim

- No hosted model was graded. Bring-your-own-key was not run against Anthropic or OpenAI in this measurement.
- Live HubSpot, Google Ads, Meta, Notion, ClickUp, and Slack accounts were not called. Parsers and the HubSpot tenant filter were checked with a mock transport.
- The 24 prompts were written against this router. A reworded request can miss. The playground says when it does not recognize a request.
- The demo OAuth issuer auto-approves. That is not an end-user login, and this report does not treat it as one.
