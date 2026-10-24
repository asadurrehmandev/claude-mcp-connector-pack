"use client";

import { AuditTable, Empty, Loading, Problem } from "@/components/records";
import { useWorkspace } from "@/components/workspace";
import { api, type AuditRow } from "@/lib/api";
import { dollars } from "@/lib/utils";
import Link from "next/link";
import { useEffect, useState } from "react";

export default function OverviewPage() {
  const { current, meta, loading, error } = useWorkspace();
  const [records, setRecords] = useState<AuditRow[] | null>(null);
  const [auditError, setAuditError] = useState("");

  useEffect(() => {
    if (!current) return;
    api
      .audit(current.id)
      .then((body) => {
        setRecords(body.records.slice(0, 6));
        setAuditError("");
      })
      .catch((reason: Error) => setAuditError(reason.message));
  }, [current]);

  if (loading) return <Loading>Loading the console.</Loading>;
  if (error) return <Problem message={error} />;
  if (!current || !meta) return <Problem message="No workspace was returned." />;

  return (
    <div className="grid gap-10">
      <section className="max-w-[68ch]">
        <h1 className="font-serif text-3xl tracking-[-0.03em] text-ink">{current.name}</h1>
        <p className="mt-3 text-base leading-7 text-ink">
          {current.summary} {current.trade} in {current.city}. Account lead {current.account_lead}. Retainer on the
          seeded SOW: {dollars(current.retainer_cents)} a month.
        </p>
        <p className="mt-3 text-sm leading-6 text-muted">{meta.mode_label} Demo clock is {meta.demo_today}.</p>
      </section>
      <section>
        <h2 className="text-lg text-ink">Connectors</h2>
        {meta.connectors.length === 0 ? (
          <Empty title="No connectors are configured">
            The server returned an empty connector list. Check the API process and reload this page.
          </Empty>
        ) : null}
        {meta.connectors.length > 0 ? (
        <>
        <ul className="mt-4 divide-y divide-rule border-y border-rule sm:hidden">
          {meta.connectors.map((row) => (
            <li key={row.id} className="grid gap-1 py-3">
              <p>{row.label}</p>
              <p className="font-mono text-xs uppercase text-muted">{row.mode}</p>
              <p className="font-mono text-xs leading-5 text-muted">{row.env}</p>
            </li>
          ))}
        </ul>
        <div className="mt-4 hidden overflow-x-auto sm:block">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="text-muted">
              <tr className="border-b border-rule">
                <th className="py-2 pr-4 font-medium">System</th>
                <th className="py-2 pr-4 font-medium">Mode</th>
                <th className="py-2 font-medium">Environment</th>
              </tr>
            </thead>
            <tbody>
              {meta.connectors.map((row) => (
                <tr key={row.id} className="border-b border-rule">
                  <td className="py-3 pr-4">{row.label}</td>
                  <td className="py-3 pr-4 font-mono text-xs uppercase">{row.mode}</td>
                  <td className="py-3 font-mono text-xs text-muted">{row.env}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </>
        ) : null}
      </section>
      <section>
        <div className="flex items-baseline justify-between gap-4">
          <h2 className="text-lg text-ink">Recent audit</h2>
          <Link href="/audit" className="text-sm text-accent">
            Full log
          </Link>
        </div>
        {auditError ? <p className="mt-3 text-sm text-accent">{auditError}</p> : null}
        {records && records.length === 0 ? (
          <Empty title="No calls for this workspace yet" href="/playground" action="Open the playground">
            The log fills when a tool runs from the playground or from an MCP client signed in as {current.name}.
          </Empty>
        ) : null}
        {records && records.length > 0 ? <AuditTable rows={records} /> : null}
      </section>
    </div>
  );
}
