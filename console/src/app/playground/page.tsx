"use client";

import { Empty, Loading, Problem } from "@/components/records";
import { Button, Input } from "@/components/ui";
import { useWorkspace } from "@/components/workspace";
import { api, type Step } from "@/lib/api";
import { useState } from "react";

type Turn = {
  id: string;
  role: "user" | "assistant";
  text: string;
  steps?: Step[];
  label?: string;
};

const SUGGESTIONS = [
  {
    label: "Halden CPA",
    workspace: "halden-bikes",
    message: "What was the Google Ads CPA for cmp_halden_exact from 2026-03-02 to 2026-03-08?",
  },
  {
    label: "Weekly report",
    workspace: "halden-bikes",
    message: "Prepare the weekly ads report for last week.",
  },
  {
    label: "Brightline claims",
    workspace: "brightline-dental",
    message: "Can we say pain-free in Brightline ads according to our docs?",
  },
  {
    label: "Find Mara",
    workspace: "halden-bikes",
    message: "Find the contact named Mara Ellison",
  },
  {
    label: "Dry-run lead",
    workspace: "halden-bikes",
    message:
      'Add a lead named Chris Adeyemi chris.adeyemi@haldenbikes.com at Halden Bikes, task "Send the media plan", assign priya.shah@fernwood.studio',
  },
];

export default function PlaygroundPage() {
  const { current, setId, loading, error } = useWorkspace();
  const [mode, setMode] = useState<"demo" | "byok">("demo");
  const [provider, setProvider] = useState("anthropic");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [message, setMessage] = useState(SUGGESTIONS[0].message);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [pending, setPending] = useState(false);
  const [pageError, setPageError] = useState("");

  async function send(text: string) {
    if (!current || !text.trim()) return;
    const userTurn: Turn = { id: crypto.randomUUID(), role: "user", text: text.trim() };
    setTurns((items) => [...items, userTurn]);
    setPending(true);
    setPageError("");
    try {
      const result = await api.playground({
        workspace_id: current.id,
        message: text.trim(),
        mode,
        provider,
        api_key: mode === "byok" ? apiKey : "",
        model,
      });
      setTurns((items) => [
        ...items,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          text: result.reply,
          steps: result.steps,
          label: result.mode_label,
        },
      ]);
    } catch (reason) {
      setPageError(reason instanceof Error ? reason.message : "The turn failed.");
    } finally {
      setPending(false);
    }
  }

  if (loading) return <Loading>Loading the playground.</Loading>;
  if (error || !current) return <Problem message={error || "No workspace."} />;

  const latest = [...turns].reverse().find((turn) => turn.role === "assistant");

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1.2fr)_minmax(280px,0.8fr)]">
      <section>
        <h1 className="font-serif text-3xl tracking-[-0.03em]">Playground</h1>
        <p className="mt-3 max-w-[62ch] text-sm leading-6 text-accent">
          {mode === "demo"
            ? "Demo mode. A scripted planner calls the mock connectors. This is not a language model."
            : "Bring-your-own-key. The key stays in this tab and is sent once per turn. It is not stored or written to the audit log."}
        </p>
        <div className="mt-5 flex flex-wrap gap-2">
          <Button variant={mode === "demo" ? "solid" : "line"} onClick={() => setMode("demo")} type="button">
            Demo planner
          </Button>
          <Button variant={mode === "byok" ? "solid" : "line"} onClick={() => setMode("byok")} type="button">
            Bring a key
          </Button>
        </div>
        {mode === "byok" ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <label className="grid gap-1 text-sm">
              Provider
              <select
                value={provider}
                onChange={(event) => setProvider(event.target.value)}
                className="h-10 rounded-sm border border-rule bg-field px-3"
              >
                <option value="anthropic">Anthropic</option>
                <option value="openai">OpenAI</option>
              </select>
            </label>
            <label className="grid gap-1 text-sm">
              Model
              <Input value={model} onChange={(event) => setModel(event.target.value)} placeholder="Leave blank for the default" />
            </label>
            <label className="grid gap-1 text-sm sm:col-span-2">
              API key
              <Input
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="Session only"
              />
            </label>
          </div>
        ) : null}
        <div className="mt-5 flex flex-wrap gap-2">
          {SUGGESTIONS.map((item) => (
            <button
              key={item.label}
              type="button"
              className="rounded-sm border border-rule px-2 py-1 text-xs text-muted"
              onClick={() => {
                setId(item.workspace);
                setMessage(item.message);
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
        <form
          className="mt-4 grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void send(message);
          }}
        >
          <label className="grid gap-1 text-sm">
            Message for {current.name}
            <textarea
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              rows={4}
              className="w-full rounded-sm border border-rule bg-field p-3 text-sm"
            />
          </label>
          <div className="flex items-center gap-3">
            <Button type="submit" disabled={pending}>
              {pending ? "Running" : "Run"}
            </Button>
            {pageError ? <p className="text-sm text-accent">{pageError}</p> : null}
          </div>
        </form>
        <div className="mt-8 grid gap-5">
          {turns.length === 0 ? (
            <Empty title="No turns in this tab yet">
              Pick a suggestion or write a request, then Run. The planner sends it to {current.name}. A write stays a dry-run until the message asks to confirm.
            </Empty>
          ) : null}
          {turns.map((turn) => (
            <article key={turn.id} className="border-t border-rule pt-4">
              <p className="font-mono text-xs uppercase text-muted">{turn.role === "user" ? "You" : "Switchboard"}</p>
              <p className="mt-2 whitespace-pre-wrap text-sm leading-6">{turn.text}</p>
              {turn.label ? <p className="mt-2 text-xs leading-5 text-muted">{turn.label}</p> : null}
            </article>
          ))}
        </div>
      </section>
      <aside className="lg:border-l lg:border-rule lg:pl-6">
        <h2 className="text-lg">Tool calls</h2>
        {!latest?.steps?.length ? (
          <Empty title="No tool call to inspect">
            Arguments, the result envelope, and the duration show up here after a turn that calls a tool. A refusal stays in the transcript and leaves this pane empty.
          </Empty>
        ) : (
          <ol className="mt-4 grid gap-4">
            {latest.steps.map((step, index) => (
              <li key={`${step.tool}-${index}`} className="border-t border-rule pt-3">
                <div className="flex items-baseline justify-between gap-3">
                  <p className="font-mono text-xs">{step.tool}</p>
                  <p className="font-mono text-xs text-muted">
                    {step.is_error ? step.error_code : "ok"} · {step.duration_ms} ms
                  </p>
                </div>
                <pre className="mt-2 max-h-64 overflow-auto font-mono text-xs leading-5 text-muted">
                  {JSON.stringify({ arguments: step.arguments, result: step.result }, null, 2)}
                </pre>
              </li>
            ))}
          </ol>
        )}
      </aside>
    </div>
  );
}
