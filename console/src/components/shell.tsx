"use client";

import { useWorkspace } from "@/components/workspace";
import { Moon, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/connections", label: "Connections" },
  { href: "/tools", label: "Tools" },
  { href: "/audit", label: "Audit" },
  { href: "/playground", label: "Playground" },
  { href: "/setup", label: "Setup" },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { workspaces, id, setId, meta, loading, error } = useWorkspace();
  const [dark, setDark] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  function toggleTheme() {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    window.localStorage.setItem("switchboard-theme", next ? "dark" : "light");
    setDark(next);
  }

  return (
    <div className="min-h-screen">
      <header className="border-b border-rule">
        <div className="mx-auto flex max-w-[1120px] flex-col gap-6 px-4 py-6 sm:px-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="font-serif text-4xl leading-none tracking-[-0.03em] text-ink">Switchboard</p>
              <p className="mt-3 max-w-xl text-sm leading-6 text-muted">
                {meta
                  ? `${meta.agency}, ${meta.agency_city}. One workspace per token. Tool results stay data.`
                  : "Fernwood Studio connector console."}
              </p>
            </div>
            <button
              type="button"
              onClick={toggleTheme}
              className="inline-flex h-10 items-center gap-2 rounded-sm border border-rule px-3 text-sm text-ink"
              aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
            >
              {dark ? <Sun size={16} strokeWidth={1.75} /> : <Moon size={16} strokeWidth={1.75} />}
              {dark ? "Light" : "Dark"}
            </button>
          </div>
          <nav className="flex flex-wrap gap-x-5 gap-y-2 text-sm" aria-label="Sections">
            {LINKS.map((link) => {
              const active = pathname === link.href;
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={active ? "text-ink underline decoration-accent decoration-2 underline-offset-8" : "text-muted"}
                  aria-current={active ? "page" : undefined}
                >
                  {link.label}
                </Link>
              );
            })}
          </nav>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div className="flex flex-wrap gap-x-5 gap-y-2" role="tablist" aria-label="Client workspace">
              {!loading && !error && workspaces.length === 0 ? (
                <p className="text-sm text-muted">No client workspaces came back from the API.</p>
              ) : null}
              {workspaces.map((item) => {
                const selected = item.id === (workspaces.some((row) => row.id === id) ? id : workspaces[0]?.id);
                return (
                  <button
                    key={item.id}
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    onClick={() => setId(item.id)}
                    className={
                      selected
                        ? "border-b-2 border-ink pb-1 text-left text-sm text-ink"
                        : "border-b-2 border-transparent pb-1 text-left text-sm text-muted"
                    }
                  >
                    {item.name}
                  </button>
                );
              })}
            </div>
            <p className="inline-flex w-fit items-center border border-rule px-2 py-1 font-mono text-xs text-muted">
              {loading ? "Loading" : error ? "API unreachable" : meta?.demo ? "Demo · mock data" : "Live credentials"}
            </p>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1120px] px-4 py-8 sm:px-6">{children}</main>
    </div>
  );
}
