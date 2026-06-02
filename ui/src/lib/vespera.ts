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

export interface ConversationItem {
  role: "user" | "assistant";
  content: string;
  [k: string]: unknown;
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

export interface MemoryStats {
  working?: number;
  recent?: number;
  validated?: number;
  core?: number;
  total_active?: number;
  [k: string]: unknown;
}

export interface StatusResponse {
  ok?: boolean;
  memory?: MemoryStats;
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
  size?: number | string;  // API returns pre-formatted string e.g. "9.0 GB"
  [k: string]: unknown;
}

export interface SecuritySettings {
  telegram_allowed_users?: string[];
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
  memories: (layer: string) => req<{ ok: boolean; memories: MemoryItem[] }>(`/api/memories?layer=${encodeURIComponent(layer)}`).then(r => r.memories),
  status: () => req<StatusResponse>("/api/status"),
  components: () => req<{ ok: boolean; components: Record<string, ComponentInfo> }>("/api/components").then(r => r.components),
  updateComponent: (name: string, body: Partial<ComponentInfo>) =>
    req<ComponentInfo>(`/api/components/${encodeURIComponent(name)}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cleanup: () => req<{ ok: boolean }>("/api/cleanup", { method: "POST" }),
  prune: () => req<{ ok: boolean }>("/api/prune", { method: "POST" }),
  models: () => req<OllamaModel[]>("/api/models"),
  conversations: (limit = 50) => req<{ ok: boolean; conversations: ConversationItem[] }>(`/api/conversations?limit=${limit}`).then(r => r.conversations),
  security: () => req<SecuritySettings>("/api/security"),
  saveTelegramUsers: (ids: string[]) =>
    req<{ ok: boolean }>("/api/settings", {
      method: "POST",
      body: JSON.stringify({ TELEGRAM_ALLOWED_USERS: ids.join(",") }),
    }),
};
