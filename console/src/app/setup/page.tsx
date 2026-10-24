"use client";

import { Problem } from "@/components/records";
import { Button } from "@/components/ui";
import { useWorkspace } from "@/components/workspace";
import { api } from "@/lib/api";
import { useEffect, useState } from "react";

export default function SetupPage() {
  const { current, meta, loading, error } = useWorkspace();
  const [snippets, setSnippets] = useState<Record<string, string> | null>(null);
  const [pageError, setPageError] = useState("");
  const [copied, setCopied] = useState("");

  useEffect(() => {
    api
      .setup()
      .then((body) => setSnippets(body.snippets))
      .catch((reason: Error) => setPageError(reason.message));
  }, []);

  async function copy(key: string, value: string) {
    await navigator.clipboard.writeText(value);
    setCopied(key);
  }

  if (loading) return <p className="text-muted">Loading setup.</p>;
  if (error || !meta) return <Problem message={error || "No setup metadata."} />;

  return (
    <div className="max-w-[78ch]">
      <h1 className="font-serif text-3xl tracking-[-0.03em]">Setup</h1>
      <p className="mt-3 text-sm leading-6 text-muted">
        Stdio is the local process Claude Desktop, Claude Code, and Cursor spawn. Streamable HTTP is the remote
        endpoint at {meta.issuer}/mcp. HTTP requires a bearer token. In demo mode the signing secret is the published
        demo secret, and the tokens below are demo credentials.
      </p>
      {current?.demo_bearer ? (
        <p className="mt-4 break-all font-mono text-xs leading-5 text-muted">
          Demo bearer for {current.name}: {current.demo_bearer}
        </p>
      ) : (
        <p className="mt-4 text-sm text-muted">Live mode does not publish a demo bearer. Mint one with the token curl below, using your own client secret.</p>
      )}
      {pageError ? <p className="mt-4 text-sm text-accent">{pageError}</p> : null}
      {snippets ? (
        <div className="mt-8 grid gap-8">
          <Snippet title="Claude Desktop (stdio)" id="claude_desktop" value={snippets.claude_desktop} copied={copied} onCopy={copy} />
          <Snippet title="Claude Code" id="claude_code" value={snippets.claude_code} copied={copied} onCopy={copy} />
          <Snippet title="Cursor (streamable HTTP)" id="cursor" value={snippets.cursor} copied={copied} onCopy={copy} />
          <Snippet title="Run HTTP locally" id="http_env" value={snippets.http_env} copied={copied} onCopy={copy} />
          <Snippet title="Run stdio locally" id="stdio_env" value={snippets.stdio_env} copied={copied} onCopy={copy} />
          <Snippet title="Demo token request" id="oauth_token" value={snippets.oauth_token} copied={copied} onCopy={copy} />
        </div>
      ) : null}
    </div>
  );
}

function Snippet({
  title,
  id,
  value,
  copied,
  onCopy,
}: {
  title: string;
  id: string;
  value: string;
  copied: string;
  onCopy: (id: string, value: string) => void;
}) {
  return (
    <section>
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-lg">{title}</h2>
        <Button type="button" variant="line" onClick={() => onCopy(id, value)}>
          {copied === id ? "Copied" : "Copy"}
        </Button>
      </div>
      <pre className="mt-3 overflow-x-auto rounded-sm border border-rule bg-field p-3 font-mono text-xs leading-5">{value}</pre>
    </section>
  );
}
