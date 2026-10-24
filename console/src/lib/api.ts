const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:4317/console";

export type Workspace = {
  id: string;
  name: string;
  trade: string;
  city: string;
  account_lead: string;
  retainer_cents: number;
  summary: string;
  demo_bearer?: string;
};

export type Connector = { id: string; label: string; mode: "mock" | "live" | string; env: string };

export type Meta = {
  service: string;
  env: string;
  demo: boolean;
  agency: string;
  agency_city: string;
  demo_today: string;
  mode_label: string;
  connectors: Connector[];
  mcp_path: string;
  resource: string;
  issuer: string;
};

export type ToolRow = {
  name: string;
  description: string;
  scope: string;
  write: boolean;
  destructive: boolean;
  enabled: boolean;
};

export type AuditRow = {
  id: string;
  ts: string;
  tenant_id: string;
  actor: string;
  tool: string;
  decision: string;
  error_code: string | null;
  duration_ms: number;
  arguments: Record<string, unknown>;
  result_summary: string;
};

export type Step = {
  tool: string;
  arguments: Record<string, unknown>;
  result: unknown;
  is_error: boolean;
  error_code: string | null;
  duration_ms: number;
};

export type PlaygroundResponse = {
  mode: string;
  mode_label: string;
  kind: string;
  reply: string;
  steps: Step[];
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const message = body?.error?.message || `Request failed (${response.status})`;
    throw new Error(message);
  }
  return body as T;
}

export const api = {
  meta: () => request<Meta>("/meta"),
  workspaces: () => request<{ workspaces: Workspace[]; demo: boolean }>("/workspaces"),
  tools: (workspaceId: string) => request<{ tools: ToolRow[] }>(`/tools?workspace_id=${encodeURIComponent(workspaceId)}`),
  toggle: (workspaceId: string, tool: string, enabled: boolean) =>
    request("/tools", { method: "PUT", body: JSON.stringify({ workspace_id: workspaceId, tool, enabled }) }),
  audit: (workspaceId: string) => request<{ records: AuditRow[] }>(`/audit?workspace_id=${encodeURIComponent(workspaceId)}&limit=40`),
  playground: (body: {
    workspace_id: string;
    message: string;
    mode: "demo" | "byok";
    provider?: string;
    api_key?: string;
    model?: string;
  }) => request<PlaygroundResponse>("/playground", { method: "POST", body: JSON.stringify(body) }),
  setup: () => request<{ snippets: Record<string, string> }>("/setup"),
};
