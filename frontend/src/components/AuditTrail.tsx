import { useEffect, useState } from "react";
import { api } from "../api";
import type { AuditRecord } from "../types";

const STATUS_CLASS: Record<AuditRecord["result_status"], string> = {
  success: "st-ok",
  pending: "st-pending",
  denied: "st-deny",
  error: "st-err",
};

export function AuditTrail() {
  const [chainValid, setChainValid] = useState<boolean | null>(null);
  const [records, setRecords] = useState<AuditRecord[] | null>(null);

  useEffect(() => {
    const load = () =>
      api
        .audit(20)
        .then((r) => {
          setChainValid(r.chain_valid);
          setRecords(r.records);
        })
        .catch(() => setRecords([]));
    load();
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, []);

  return (
    <section className="panel" aria-label="سجل التدقيق">
      <h2>
        سجل التدقيق
        <span className={`badge ${chainValid ? "badge-r0" : "badge-r4"}`}>
          {chainValid === null ? "…" : chainValid ? "سلسلة سليمة" : "سلسلة مكسورة"}
        </span>
      </h2>
      {records === null ? (
        <p className="muted">جارٍ التحميل…</p>
      ) : records.length === 0 ? (
        <p className="muted">لا توجد سجلات بعد.</p>
      ) : (
        <table className="audit-table">
          <thead>
            <tr>
              <th>#</th>
              <th>الأداة</th>
              <th>القرار</th>
              <th>الحالة</th>
              <th>الوقت</th>
            </tr>
          </thead>
          <tbody>
            {records.map((r) => (
              <tr key={r.audit_id}>
                <td>{r.audit_id}</td>
                <td>
                  <code>{r.tool_name}</code>
                </td>
                <td className="muted">{r.policy_decision}</td>
                <td>
                  <span className={`status ${STATUS_CLASS[r.result_status]}`}>
                    {r.result_status}
                  </span>
                  {r.error_code && <small className="err-code"> {r.error_code}</small>}
                </td>
                <td className="muted">{new Date(r.start_time).toLocaleTimeString("ar-EG")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
