import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { ConversationState, DecisionSignal, Status, Turn } from "./state";


function statusTone(s: Status | undefined): "ok" | "warn" | "bad" | "neutral" {
  switch (s) {
    case "accepted":
    case "confirmed_execution":
    case "replay":
      return "ok";
    case "confirmation_required":
    case "awaiting_signature":
      return "warn";
    case "denied":
    case "erp_error":
    case "validation_error":
    case "conflict":
    case "decision_quarantined":
      return "bad";
    case "decision_clarification":
      return "warn";
    default:
      return "neutral";
  }
}

function signalLabel(signal: DecisionSignal | null | undefined): string | null {
  if (!signal?.active) return null;
  const bits = ["إشارة · استشارية فقط", signal.provider, signal.mode, signal.route?.tool].filter(Boolean);
  return bits.join(" · ");
}

function TurnBubble({ turn }: { turn: Turn }) {
  const tone = statusTone(turn.status);
  const signal = signalLabel(turn.decision);
  return (
    <article className={`turn turn-${turn.role} tone-${tone}`}>
      <header>
        <span className="who">{turn.role === "user" ? "أنت" : "⚖️ ميزان"}</span>
        {turn.status ? <span className={`badge tone-${tone}`}>{turn.status}</span> : null}
      </header>
      <div className="bubble" dir="auto">{turn.text}</div>
      {signal ? <p className="signal-note" title={turn.decision?.notice}>{signal}</p> : null}
    </article>
  );
}

export function App() {
  const [sessionId] = useState<string>(() => crypto.randomUUID());
  const [state, setState] = useState<ConversationState | null>(null);
  const [input, setInput] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decisionChip, setDecisionChip] = useState<string>("إشارة: …");

  useEffect(() => {
    api.me()
      .then((me) =>
        setState({
          session_id: sessionId,
          user_id: me.user_id,
          tenant_id: me.tenant_id,
          status: "idle",
          turns: [],
          active_proposal: null,
          lease_until_ms: null,
        }),
      )
      .catch((e) => setError(String(e)));
    api.decision()
      .then((payload) => {
        const d = payload.decision || {};
        const provider = d.config?.provider || d.provider || "off";
        const mode = d.config?.mode || d.mode || "off";
        setDecisionChip(d.enabled ? `إشارة: ${provider}/${mode} · استشارية فقط` : "إشارة: متوقفة");
      })
      .catch(() => setDecisionChip("إشارة: —"));
  }, [sessionId]);

  async function submit(text: string) {
    if (!state || !text.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const next = await api.send(state, text.trim());
      setState(next);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      setInput("");
    }
  }

  async function onConfirm(signed?: string) {
    if (!state) return;
    setBusy(true);
    try {
      const next = await api.confirm(state, signed);
      setState(next);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDecline() {
    if (!state) return;
    setBusy(true);
    try {
      const next = await api.decline(state);
      setState(next);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const turns = useMemo(() => state?.turns ?? [], [state]);
  const awaitingConfirmation = state?.active_proposal && state.status === "confirmation_required";

  return (
    <main className="app">
      <header className="topbar">
        <h1>⚖️ ميزان · Mizan</h1>
        <div className="meta">
          <span>مستخدم: <bdo dir="ltr">{state?.user_id ?? "…"}</bdo></span>
          <span>الحالة: <em>{state?.status ?? "…"}</em></span>
          <span className="signal-chip" title="إشارة القرار لا تُصرّح ولا تُنفّذ ولا تُؤكّد.">{decisionChip}</span>
        </div>
      </header>
      {error ? <div className="error-banner" role="alert">{error}</div> : null}
      <section className="transcript">
        {turns.length === 0 ? (
          <p className="placeholder">اكتب طلبك (مثلاً: «اعرض العملاء اللي اسمهم محمد» أو «أنشئ أمر بيع للعميل 42 بكمية 2 من المنتج 55».)</p>
        ) : (
          turns.map((t) => <TurnBubble key={t.id} turn={t} />)
        )}
      </section>
      {awaitingConfirmation ? (
        <section className="confirm-card" role="region" aria-label="تأكيد مقترح">
          <p>مقترح في انتظار تأكيدك على <code>{state!.active_proposal!.tool}</code>.</p>
          <div className="actions">
            <button disabled={busy} onClick={() => onConfirm()}>تأكيد ✓</button>
            <button disabled={busy} onClick={onDecline}>رفض ✗</button>
          </div>
        </section>
      ) : null}
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          submit(input);
        }}
      >
        <textarea
          rows={2}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="اكتب طلبك…"
          disabled={busy || !state || !!awaitingConfirmation}
          dir="auto"
        />
        <button type="submit" disabled={busy || !input.trim() || !!awaitingConfirmation}>
          {busy ? "جاري التنفيذ…" : "إرسال"}
        </button>
      </form>
      <footer className="foot">
        <small>Mizan · fail-closed · server-authoritative · العربية أولاً.</small>
      </footer>
    </main>
  );
}
