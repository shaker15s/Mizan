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
  | "text_only";

export interface Turn {
  id: string;
  role: "user" | "assistant";
  text: string;
  status?: Status;
  proposal?: ProposalSnapshot | null;
  tool?: string | null;
  error_code?: string | null;
  created_at: string;
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
