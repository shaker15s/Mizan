// Canonical execution state machine (mirrors server-side Phase 2 SM).
// The UI never invents states; it renders whatever the gateway reports.
export type Status =
  | "idle"
  | "routing"
  | "policy_check"
  | "confirmation_required"
  | "awaiting_signature"
  | "executing"
  | "erp_error"
  | "validation_error"
  | "denied"
  | "accepted"
  | "confirmed_execution"
  | "replay"
  | "conflict"
  | "text_only"
  | "decision_quarantined"
  | "decision_clarification";

export interface DecisionSignal {
  active: boolean;
  authority: "signal_only";
  notice?: string;
  mode?: string;
  provider?: string;
  latency_ms?: number;
  route?: { tool?: string; confidence?: number; margin?: number } | null;
  narrowed?: boolean;
  escalation_level?: string;
  fallback?: boolean;
}

export interface Turn {
  id: string;
  role: "user" | "assistant";
  text: string;
  status?: Status;
  proposal?: ProposalSnapshot | null;
  tool?: string | null;
  error_code?: string | null;
  created_at: string;
  decision?: DecisionSignal | null;
}

export interface ProposalSnapshot {
  proposal_id: string;
  tool: string;
  arguments: Record<string, unknown>;
  version: number;
  expires_at: string;
  requires_signature: boolean;
}

export interface ConversationState {
  session_id: string;
  user_id: string;
  tenant_id: string;
  status: Status;
  turns: Turn[];
  active_proposal: ProposalSnapshot | null;
  lease_until_ms: number | null;
}

export const TERMINAL_STATUSES: ReadonlySet<Status> = new Set([
  "accepted",
  "confirmed_execution",
  "denied",
  "erp_error",
  "validation_error",
  "replay",
  "conflict",
  "text_only",
]);

export function isTerminal(s: Status): boolean {
  return TERMINAL_STATUSES.has(s);
}
