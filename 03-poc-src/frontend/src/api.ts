// Thin typed client for the Mizan backend. No secrets stored in the client;
// auth is handled via HttpOnly session cookies supplied by the server.
import type { ConversationState, Turn, ProposalSnapshot, Status, DecisionSignal } from "./state";

export interface ApiError extends Error {
  status: number;
  code?: string;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(init.headers || {}) },
    ...init,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) {
    const err = new Error(data.reason || res.statusText) as ApiError;
    err.status = res.status;
    err.code = data.error_code;
    throw err;
  }
  return data as T;
}

function _id() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return Math.random().toString(36).slice(2);
}

// The legacy /api/chat payload carries response_ar + status + proposal but not
// a full turns array. We synthesise a user+assistant turn pair so the React
// UI can drive off a single ConversationState without changing the backend
// contract today. Phase 11/12 can replace this with a proper streaming shape.
function _turn(role: Turn["role"], text: string, extra: Partial<Turn> = {}): Turn {
  return {
    id: _id(),
    role,
    text,
    status: extra.status,
    proposal: extra.proposal ?? null,
    tool: extra.tool ?? null,
    error_code: extra.error_code ?? null,
    created_at: new Date().toISOString(),
    decision: extra.decision ?? null,
  };
}

export interface ChatResponse {
  status: Status;
  response_ar: string;
  response_markdown?: string;
  proposal?: ProposalSnapshot | null;
  tool?: string | null;
  error?: { code?: string; message?: string } | null;
  session_id: string;
  decision?: DecisionSignal | null;
}

export interface DecisionHealth {
  success: boolean;
  decision: {
    enabled: boolean;
    authority: string;
    authority_notice?: string;
    provider?: string;
    mode?: string;
    config?: { provider?: string; mode?: string; model?: string | null };
    refusal_reason?: string;
  };
}

function _mergeTurns(prev: Turn[], user: Turn, assistant: Turn): Turn[] {
  // Keep bounded history to prevent unbounded growth client-side.
  const MAX = 100;
  const next = [...prev, user, assistant];
  return next.length > MAX ? next.slice(next.length - MAX) : next;
}

export const api = {
  me(): Promise<{ user_id: string; tenant_id: string; role: string }> {
    return request("/api/me");
  },
  decision(): Promise<DecisionHealth> {
    return request("/api/decision");
  },
  async send(state: ConversationState, text: string): Promise<ConversationState> {
    const payload = await request<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ session_id: state.session_id, input: text }),
    });
    const userTurn = _turn("user", text);
    const assistantTurn = _turn("assistant", payload.response_ar || payload.response_markdown || "", {
      status: payload.status,
      tool: payload.tool ?? null,
      proposal: payload.proposal ?? null,
      error_code: payload.error?.code ?? null,
      decision: payload.decision ?? null,
    });
    return {
      ...state,
      status: payload.status,
      turns: _mergeTurns(state.turns, userTurn, assistantTurn),
      active_proposal: payload.proposal ?? null,
      lease_until_ms: null,
    };
  },
  async confirm(state: ConversationState, signed_token?: string): Promise<ConversationState> {
    if (!state.active_proposal) return state;
    const payload = await request<ChatResponse>("/api/confirm", {
      method: "POST",
      body: JSON.stringify({ proposal_id: state.active_proposal.proposal_id, signed_token: signed_token ?? null }),
    });
    const turn = _turn("assistant", payload.response_ar || payload.response_markdown || "", {
      status: payload.status,
      tool: payload.tool ?? null,
      proposal: null,
      error_code: payload.error?.code ?? null,
    });
    return {
      ...state,
      status: payload.status,
      turns: [...state.turns, turn],
      active_proposal: payload.proposal ?? null,
    };
  },
  async decline(state: ConversationState): Promise<ConversationState> {
    if (!state.active_proposal) return state;
    const payload = await request<ChatResponse>("/api/decline", {
      method: "POST",
      body: JSON.stringify({ proposal_id: state.active_proposal.proposal_id }),
    });
    const turn = _turn("assistant", payload.response_ar || payload.response_markdown || "تم رفض المقترح.", {
      status: payload.status,
      error_code: payload.error?.code ?? null,
    });
    return {
      ...state,
      status: payload.status,
      turns: [...state.turns, turn],
      active_proposal: null,
    };
  },
  history(_session_id: string): Promise<{ turns: Turn[]; active_proposal: ProposalSnapshot | null }> {
    return Promise.resolve({ turns: [], active_proposal: null });
  },
};
