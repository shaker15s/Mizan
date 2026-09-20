/**
 * Thin typed API client for the MIZAN backend.
 *
 * All fetches are same-origin (vite proxies /api → Python server in dev; in
 * production the Python server serves the built `dist/` directly). No
 * credentials or tokens are stored client-side yet; Phase 9b will wire
 * SameSite=strict session cookies + CSRF tokens and this file will add the
 * CSRF header on non-GET requests.
 */
import type {
  AuditRecord,
  GatewayResponse,
  Telemetry,
  ToolContract,
} from "./types";

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(path, { ...init, headers, credentials: "same-origin" });
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`MIZAN: non-JSON response from ${path} (status ${res.status})`);
  }
  if (!res.ok) {
    const msg =
      (body as { error?: { message?: string } })?.error?.message ??
      `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return body as T;
}

export const api = {
  health: () => request<{ status: string; version: string }>("/api/health"),
  telemetry: () => request<Telemetry>("/api/telemetry"),
  tools: () =>
    request<{ success: boolean; count: number; tools: ToolContract[] }>(
      "/api/tools",
    ),
  chat: (message: string) =>
    request<GatewayResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  confirm: (proposal_id: string) =>
    request<GatewayResponse>("/api/confirm", {
      method: "POST",
      body: JSON.stringify({ proposal_id }),
    }),
  decline: (proposal_id: string) =>
    request<GatewayResponse>("/api/decline", {
      method: "POST",
      body: JSON.stringify({ proposal_id }),
    }),
  audit: (limit = 50) =>
    request<{
      success: boolean;
      chain_valid: boolean;
      records: AuditRecord[];
    }>(`/api/audit?limit=${limit}`),
};
