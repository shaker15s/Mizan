import { useEffect, useState } from "react";
import { api } from "../api";
import type { ToolContract } from "../types";

const RISK_BADGE: Record<ToolContract["risk_level"], string> = {
  R0: "badge-r0",
  R1: "badge-r1",
  R2: "badge-r2",
  R3: "badge-r3",
  R4: "badge-r4",
};

export function ToolsPanel() {
  const [tools, setTools] = useState<ToolContract[] | null>(null);
  useEffect(() => {
    api.tools().then((r) => setTools(r.tools)).catch(() => setTools([]));
  }, []);
  return (
    <section className="panel" aria-label="سجل الأدوات">
      <h2>الأدوات المتاحة</h2>
      {tools === null ? (
        <p className="muted">جارٍ التحميل…</p>
      ) : (
        <ul className="tool-list">
          {tools.map((t) => (
            <li key={t.name}>
              <div className="tool-head">
                <code>{t.name}</code>
                <span className={`badge ${RISK_BADGE[t.risk_level]}`}>{t.risk_level}</span>
              </div>
              <p className="muted">{t.description_ar || t.description}</p>
              <small className="muted">
                v{t.tool_version} · {t.readOnly ? "قراءة فقط" : "تتطلب كتابة"}
                {t.requiresConfirmation && " · تستلزم التأكيد"}
                {t.destructive && " · عملية مدمّرة"}
              </small>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
