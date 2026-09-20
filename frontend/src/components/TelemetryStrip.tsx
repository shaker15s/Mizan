import type { Telemetry } from "../types";

interface Props {
  telemetry: Telemetry | null;
  error: string | null;
}

export function TelemetryStrip({ telemetry, error }: Props) {
  if (error) {
    return (
      <div className="tel tel-error" role="status">
        <span className="dot dot-red" />
        <span>غير متصل بالخادم — {error}</span>
      </div>
    );
  }
  if (!telemetry) {
    return (
      <div className="tel" role="status">
        <span className="dot dot-grey" />
        <span>جارٍ الاتصال…</span>
      </div>
    );
  }
  const healthy = telemetry.status === "online" && telemetry.odoo.reachable;
  return (
    <div className="tel" role="status">
      <span className={`dot ${healthy ? "dot-green" : "dot-amber"}`} />
      <span>
        الحالة: <b>{telemetry.status === "online" ? "على الهواء" : telemetry.status}</b>
      </span>
      <span className="sep">·</span>
      <span>الدور: {telemetry.security.role}</span>
      <span className="sep">·</span>
      <span>
        Odoo: {telemetry.odoo.reachable ? `متصل (${telemetry.odoo.latency_ms ?? "?"}ms)` : "غير متصل"}
      </span>
      <span className="sep">·</span>
      <span>
        قاطع الدائرة: {telemetry.circuit.state}
      </span>
    </div>
  );
}
