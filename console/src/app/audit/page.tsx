"use client";

import { AuditTable, Problem } from "@/components/records";
import { useWorkspace } from "@/components/workspace";
import { api, type AuditRow } from "@/lib/api";
import { useEffect, useState } from "react";

export default function AuditPage() {
  const { current, loading, error } = useWorkspace();
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [pageError, setPageError] = useState("");

  useEffect(() => {
    if (!current) return;
    setRows(null);
    api
      .audit(current.id)
      .then((body) => {
        setRows(body.records);
        setPageError("");
      })
      .catch((reason: Error) => setPageError(reason.message));
  }, [current]);

  if (loading) return <p className="text-muted">Loading the audit log.</p>;
  if (error || !current) return <Problem message={error || "No workspace."} />;

  return (
    <div>
      <h1 className="font-serif text-3xl tracking-[-0.03em]">Audit · {current.name}</h1>
      <p className="mt-3 max-w-[68ch] text-sm leading-6 text-muted">
        Arguments are stored after redaction. Emails are masked, and bearer tokens, sb1 tokens, and sk- keys are
        removed. Note bodies are not copied into the log; the summary says whether the call was a dry-run, an allow,
        or a denial.
      </p>
      {pageError ? <p className="mt-4 text-sm text-accent">{pageError}</p> : null}
      {rows && rows.length === 0 ? (
        <p className="mt-6 text-sm text-muted">No audit rows for this workspace yet.</p>
      ) : null}
      {rows && rows.length > 0 ? <AuditTable rows={rows} /> : null}
      {rows && rows.length > 0 ? (
        <div className="mt-8 grid gap-4">
          {rows.slice(0, 3).map((row) => (
            <pre key={`${row.id}-args`} className="overflow-x-auto rounded-sm border border-rule bg-field p-3 font-mono text-xs leading-5 text-ink">
              {row.tool} {row.decision}
              {"\n"}
              {JSON.stringify(row.arguments, null, 2)}
            </pre>
          ))}
        </div>
      ) : null}
    </div>
  );
}
