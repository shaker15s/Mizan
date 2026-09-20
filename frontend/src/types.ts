/**
 * Shared TypeScript types for the MIZAN cockpit.
 *
 * These mirror the canonical server responses — the server is the source of
 * truth; clients never invent IDs or success state (plan §22 truthfulness
 * contract). Every field comes from JSON; no field is optional unless the
 * server explicitly emits `null` for it.
 */

export type ExecutionStatus =
  | "accepted"
  | "in_progress"
  | "confirmation_required"
  | "denied"
  | "declined"
  | "conflict"
  | "replay"
  | "reconciliation_required"
  | "erp_error"
  | "validation_error";

export interface StructuredError {
  code: string;
  message: string;
  http_status: number;
  user_message_ar: string;
  retryable: boolean;
}

export interface GatewayResponse {
  success?: boolean;
  status?: ExecutionStatus;
  error?: { code: string; message: string };
  structured_error?: StructuredError;
  response_ar?: string;
  result?: Record<string, unknown>;
  proposal?: Proposal;
  requires_confirmation?: boolean;
  execution_id?: string;
  idempotency_key?: string;
  audit_id?: number;
  policy_decision?: string;
}

export interface Proposal {
  proposal_id: string;
  tool_name: string;
  tool_version: string;
  arguments: Record<string, unknown>;
  operation_hash: string;
  user_id: string;
  tenant_id: string;
  created_at: string;
  expires_at: string;
}

export interface ToolContract {
  name: string;
  tool_version: string;
  description: string;
  description_ar: string;
  readOnly: boolean;
  destructive: boolean;
  risk_level: "R0" | "R1" | "R2" | "R3" | "R4";
  requiresConfirmation: boolean;
  inputSchema: Record<string, unknown>;
}

export interface Telemetry {
  status: "online" | "degraded" | "offline";
  odoo: { reachable: boolean; latency_ms: number | null };
  circuit: { state: "closed" | "open" | "half_open"; failures: number };
  security: {
    role: string;
    csp_enforced: boolean;
    cors_strict: boolean;
    rate_limit_per_minute: number;
  };
  uptime_seconds: number;
}

export interface AuditRecord {
  audit_id: number;
  request_id: string;
  tool_name: string;
  result_status: "success" | "pending" | "denied" | "error";
  error_code: string | null;
  policy_decision: string;
  idempotency_key: string | null;
  execution_id: string | null;
  proposal_id: string | null;
  operation_hash: string | null;
  external_record_id: string | null;
  start_time: string;
  end_time: string;
}
