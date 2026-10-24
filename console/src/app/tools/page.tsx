"use client";

import { Problem } from "@/components/records";
import { Switch } from "@/components/ui";
import { useWorkspace } from "@/components/workspace";
import { api, type ToolRow } from "@/lib/api";
import { useEffect, useState } from "react";

export default function ToolsPage() {
  const { current, loading, error } = useWorkspace();
  const [tools, setTools] = useState<ToolRow[] | null>(null);
  const [pageError, setPageError] = useState("");
  const [pending, setPending] = useState("");

  useEffect(() => {
    if (!current) return;
    setTools(null);
    api
      .tools(current.id)
      .then((body) => {
        setTools(body.tools);
        setPageError("");
      })
      .catch((reason: Error) => setPageError(reason.message));
  }, [current]);

  async function toggle(tool: ToolRow, enabled: boolean) {
    if (!current) return;
    setPending(tool.name);
    setTools((rows) => rows?.map((row) => (row.name === tool.name ? { ...row, enabled } : row)) ?? rows);
    try {
      await api.toggle(current.id, tool.name, enabled);
      setPageError("");
    } catch (reason) {
      setTools((rows) => rows?.map((row) => (row.name === tool.name ? { ...row, enabled: tool.enabled } : row)) ?? rows);
      setPageError(reason instanceof Error ? reason.message : "The toggle did not save.");
    } finally {
      setPending("");
    }
  }

  if (loading) return <p className="text-muted">Loading tools.</p>;
  if (error || !current) return <Problem message={error || "No workspace."} />;

  return (
    <div>
      <h1 className="font-serif text-3xl tracking-[-0.03em]">Tools for {current.name}</h1>
      <p className="mt-3 max-w-[68ch] text-sm leading-6 text-muted">
        A workspace with no saved policy is read-only: write tools stay off. These demo workspaces ship with tools
        enabled so the sample flows can run. Enabling a write tool does not skip the confirm flag. A call with confirm
        false returns a dry-run and changes nothing.
      </p>
      {pageError ? <p className="mt-4 text-sm text-accent">{pageError}</p> : null}
      {!tools ? <p className="mt-6 text-sm text-muted">Loading the tool list.</p> : null}
      {tools ? (
        <>
        <ul className="mt-6 divide-y divide-rule border-y border-rule sm:hidden">
          {tools.map((tool) => (
            <li key={`${tool.name}-m`} className="flex items-start justify-between gap-4 py-4">
              <div>
                <p className="font-mono text-xs">{tool.name}</p>
                <p className="mt-1 text-sm text-muted">
                  {tool.scope} · {tool.write ? (tool.destructive ? "Write, destructive" : "Write") : "Read"}
                </p>
                <p className="mt-2 text-sm leading-6 text-muted">{tool.description}</p>
              </div>
              <Switch
                checked={tool.enabled}
                disabled={pending === tool.name}
                onCheckedChange={(value) => toggle(tool, value)}
                label={`${tool.enabled ? "Disable" : "Enable"} ${tool.name}`}
              />
            </li>
          ))}
        </ul>
        <div className="mt-6 hidden overflow-x-auto sm:block">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="text-muted">
              <tr className="border-b border-rule">
                <th className="py-2 pr-4 font-medium">Tool</th>
                <th className="py-2 pr-4 font-medium">Scope</th>
                <th className="py-2 pr-4 font-medium">Kind</th>
                <th className="py-2 font-medium">On</th>
              </tr>
            </thead>
            <tbody>
              {tools.map((tool) => (
                <tr key={tool.name} className="border-b border-rule align-top">
                  <td className="py-3 pr-4">
                    <p className="font-mono text-xs">{tool.name}</p>
                    <p className="mt-1 max-w-[46ch] text-muted">{tool.description}</p>
                  </td>
                  <td className="py-3 pr-4 font-mono text-xs">{tool.scope}</td>
                  <td className="py-3 pr-4">{tool.write ? (tool.destructive ? "Write, destructive" : "Write") : "Read"}</td>
                  <td className="py-3">
                    <Switch
                      checked={tool.enabled}
                      disabled={pending === tool.name}
                      onCheckedChange={(value) => toggle(tool, value)}
                      label={`${tool.enabled ? "Disable" : "Enable"} ${tool.name}`}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </>
      ) : null}
    </div>
  );
}
