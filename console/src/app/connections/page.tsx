"use client";

import { Problem } from "@/components/records";
import { useWorkspace } from "@/components/workspace";

export default function ConnectionsPage() {
  const { meta, loading, error } = useWorkspace();
  if (loading) return <p className="text-muted">Loading connections.</p>;
  if (error || !meta) return <Problem message={error || "No connector status."} />;

  return (
    <div className="max-w-[72ch]">
      <h1 className="font-serif text-3xl tracking-[-0.03em]">Connections</h1>
      <p className="mt-3 text-sm leading-6 text-muted">
        Vendor keys stay in the server environment. This page reports whether each connector is using the mock
        backend or a live HTTP client. A pasted model key in the playground is separate, and it is not one of these
        credentials.
      </p>
      <ul className="mt-8 divide-y divide-rule border-y border-rule">
        {meta.connectors.map((row) => (
          <li key={row.id} className="grid gap-2 py-4 sm:grid-cols-[180px_80px_1fr] sm:items-baseline">
            <p className="text-ink">{row.label}</p>
            <p className="font-mono text-xs uppercase text-muted">{row.mode}</p>
            <p className="font-mono text-xs leading-5 text-muted">{row.env}</p>
          </li>
        ))}
      </ul>
      <p className="mt-6 text-sm leading-6 text-muted">
        Live mode also needs a tenant map: a Google Ads customer id, a Meta ad account, Notion page ids, a ClickUp
        list, or a Slack channel allowlist. Without the map, that connector refuses the call instead of reading
        another workspace&apos;s account.
      </p>
    </div>
  );
}
