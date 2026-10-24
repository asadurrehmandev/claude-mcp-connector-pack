"use client";

import type { AuditRow } from "@/lib/api";
import Link from "next/link";
import type { ReactNode } from "react";

export function Loading({ children }: { children: ReactNode }) {
  return (
    <p className="text-sm text-muted" role="status">
      {children}
    </p>
  );
}

export function Empty({
  title,
  children,
  href,
  action,
}: {
  title: string;
  children: ReactNode;
  href?: string;
  action?: string;
}) {
  return (
    <div className="mt-6 max-w-[62ch] border border-rule bg-field px-4 py-5">
      <p className="text-base text-ink">{title}</p>
      <p className="mt-2 text-sm leading-6 text-muted">{children}</p>
      {href && action ? (
        <Link href={href} className="mt-3 inline-block text-sm text-accent underline decoration-accent/40 underline-offset-4">
          {action}
        </Link>
      ) : null}
    </div>
  );
}

export function AuditTable({ rows }: { rows: AuditRow[] }) {
  return (
    <div className="mt-4">
      <ul className="divide-y divide-rule border-y border-rule sm:hidden">
        {rows.map((row) => (
          <li key={`${row.id}-${row.ts}-${row.tool}-m`} className="grid gap-1 py-3 text-sm">
            <p className="font-mono text-xs">{row.tool}</p>
            <p>
              {row.decision}
              {row.error_code ? ` · ${row.error_code}` : ""} · {row.duration_ms} ms
            </p>
            <p className="text-muted">{row.error_code || row.result_summary}</p>
            <p className="font-mono text-xs text-muted">{row.ts.replace("T", " ").replace("Z", "")}</p>
          </li>
        ))}
      </ul>
      <div className="hidden overflow-x-auto sm:block">
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead className="text-muted">
            <tr className="border-b border-rule">
              <th className="py-2 pr-4 font-medium">When</th>
              <th className="py-2 pr-4 font-medium">Tool</th>
              <th className="py-2 pr-4 font-medium">Decision</th>
              <th className="py-2 pr-4 font-medium">Summary</th>
              <th className="py-2 font-medium">ms</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.id}-${row.ts}-${row.tool}`} className="border-b border-rule">
                <td className="py-3 pr-4 font-mono text-xs text-muted">{row.ts.replace("T", " ").replace("Z", "")}</td>
                <td className="py-3 pr-4 font-mono text-xs">{row.tool}</td>
                <td className="py-3 pr-4">{row.decision}</td>
                <td className="py-3 pr-4">{row.error_code || row.result_summary}</td>
                <td className="py-3 font-mono text-xs">{row.duration_ms}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function Problem({ message }: { message: string }) {
  return (
    <div className="max-w-[62ch]">
      <h1 className="font-serif text-3xl tracking-[-0.03em]">The console cannot reach the API</h1>
      <p className="mt-3 text-sm leading-6 text-muted">{message}</p>
      <p className="mt-3 font-mono text-xs leading-6 text-ink">python -m switchboard http --port 4317</p>
    </div>
  );
}
