"use client";

import { api, type Meta, type Workspace } from "@/lib/api";
import { createContext, useContext, useEffect, useMemo, useState } from "react";

type WorkspaceState = {
  id: string;
  setId: (id: string) => void;
  workspaces: Workspace[];
  current: Workspace | undefined;
  meta: Meta | null;
  loading: boolean;
  error: string;
  refresh: () => void;
};

const Ctx = createContext<WorkspaceState | null>(null);

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const [id, setIdState] = useState("halden-bikes");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const saved = window.localStorage.getItem("switchboard-workspace");
    if (saved) setIdState(saved);
  }, []);

  useEffect(() => {
    let cancel = false;
    setLoading(true);
    Promise.all([api.meta(), api.workspaces()])
      .then(([nextMeta, nextWorkspaces]) => {
        if (cancel) return;
        setMeta(nextMeta);
        setWorkspaces(nextWorkspaces.workspaces);
        setError("");
      })
      .catch((reason: Error) => {
        if (!cancel) setError(reason.message || "The console API did not respond.");
      })
      .finally(() => {
        if (!cancel) setLoading(false);
      });
    return () => {
      cancel = true;
    };
  }, [tick]);

  const value = useMemo<WorkspaceState>(() => {
    return {
      id,
      setId: (next) => {
        setIdState(next);
        window.localStorage.setItem("switchboard-workspace", next);
      },
      workspaces,
      current: workspaces.find((item) => item.id === id) ?? workspaces[0],
      meta,
      loading,
      error,
      refresh: () => setTick((n) => n + 1),
    };
  }, [id, workspaces, meta, loading, error]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWorkspace() {
  const value = useContext(Ctx);
  if (!value) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return value;
}
