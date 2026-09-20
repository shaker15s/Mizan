import { useEffect, useState } from "react";
import { api } from "./api";
import { ChatPanel } from "./components/ChatPanel";
import { TelemetryStrip } from "./components/TelemetryStrip";
import { ToolsPanel } from "./components/ToolsPanel";
import { AuditTrail } from "./components/AuditTrail";
import type { Telemetry } from "./types";

export function App() {
  const [telemetry, setTelemetry] = useState<Telemetry | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = () =>
      api
        .telemetry()
        .then((t) => {
          if (!cancelled) {
            setTelemetry(t);
            setError(null);
          }
        })
        .catch((e: Error) => !cancelled && setError(e.message));
    tick();
    const handle = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, []);

  return (
    <main className="app-shell" dir="rtl">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark">م</span>
          <div>
            <h1>MIZAN · ميزان</h1>
            <p className="tagline">
              وكيل تخاطب عربي لـ Odoo، بتحكم حكومي رشيد لا يكتب أمراً بدون توقيع.
            </p>
          </div>
        </div>
        <TelemetryStrip telemetry={telemetry} error={error} />
      </header>

      <section className="app-grid">
        <ChatPanel />
        <aside className="app-side">
          <ToolsPanel />
          <AuditTrail />
        </aside>
      </section>

      <footer className="app-footer">
        <span>
          بنية تنفيذ قانونية · سجل تدقيق متسلسل تجزئياً · لا ثقة أعمى في خرج النموذج
        </span>
      </footer>
    </main>
  );
}
