// API port — set VITE_API_PORT in .env.local to override
export const API_BASE = `http://localhost:${import.meta.env.VITE_API_PORT ?? "5055"}`;

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

export interface ChatResponse {
  response: string;
  model?: string;
  source?: "local" | "cloud" | string;
  complexity?: number;
  audio?: string;
  [k: string]: unknown;
}

export interface MemoryItem {
  id?: string | number;
  content: string;
  trust_score?: number;
  created_at?: string;
  layer?: string;
  [k: string]: unknown;
}

export interface StatusResponse {
  working?: number;
  recent?: number;
  validated?: number;
  core?: number;
  [k: string]: unknown;
}

export interface ComponentInfo {
  name: string;
  description?: string;
  model?: string;
  api_key?: string;
  [k: string]: unknown;
}

export interface OllamaModel {
  name: string;
  size?: number;
  [k: string]: unknown;
}

export interface SecuritySettings {
  telegram_allowed_user_ids?: string;
  shell_execution?: boolean;
  allowed_file_paths?: string;
  api_auth_token?: string;
  max_tokens?: number;
  [k: string]: unknown;
}

export const vespera = {
  chat: (message: string) =>
    req<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  memories: (layer: string) => req<MemoryItem[]>(`/api/memories?layer=${encodeURIComponent(layer)}`),
  status: () => req<StatusResponse>("/api/status"),
  components: () => req<ComponentInfo[]>("/api/components"),
  updateComponent: (name: string, body: Partial<ComponentInfo>) =>
    req<ComponentInfo>(`/api/components/${encodeURIComponent(name)}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cleanup: () => req<{ ok: boolean }>("/api/cleanup", { method: "POST" }),
  prune: () => req<{ ok: boolean }>("/api/prune", { method: "POST" }),
  models: () => req<OllamaModel[]>("/api/models"),
  security: () => req<SecuritySettings>("/api/security"),
  updateSecurity: (body: Partial<SecuritySettings>) =>
    req<SecuritySettings>("/api/security", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
