import { useState } from "react";
import { api } from "../api";
import type { GatewayResponse } from "../types";

interface Turn {
  id: string;
  kind: "user" | "assistant" | "proposal" | "error";
  text?: string;
  response?: GatewayResponse;
}

function initialTurn(): Turn {
  return {
    id: "intro",
    kind: "assistant",
    text:
      "أهلاً بيك في MIZAN. اكتب طلبك بلغة طبيعية — مثل «اعمل أمر بيع للعميل 42 بصنف كمية 10 وسعر 200» — وأنا هرتب الإجراء، وأطلب توقيعك قبل أي كتابة فعلية في Odoo.",
  };
}

export function ChatPanel() {
  const [turns, setTurns] = useState<Turn[]>([initialTurn()]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const append = (t: Omit<Turn, "id">) =>
    setTurns((prev) => [...prev, { id: crypto.randomUUID(), ...t }]);

  const send = async () => {
    const text = message.trim();
    if (!text || busy) return;
    setBusy(true);
    append({ kind: "user", text });
    setMessage("");
    try {
      const resp = await api.chat(text);
      append({ kind: classifyTurn(resp), text: resp.response_ar, response: resp });
    } catch (e) {
      append({
        kind: "error",
        text: `تعذَّر إكمال الطلب: ${(e as Error).message}`,
      });
    } finally {
      setBusy(false);
    }
  };

  const confirm = async (proposalId: string) => {
    setBusy(true);
    try {
      const resp = await api.confirm(proposalId);
      append({ kind: classifyTurn(resp), text: resp.response_ar, response: resp });
    } catch (e) {
      append({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const decline = async (proposalId: string) => {
    setBusy(true);
    try {
      const resp = await api.decline(proposalId);
      append({ kind: classifyTurn(resp), text: resp.response_ar, response: resp });
    } catch (e) {
      append({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel chat-panel" aria-label="لوحة المحادثة">
      <ol className="turns">
        {turns.map((t) => (
          <li key={t.id} className={`turn turn-${t.kind}`}>
            <div className="bubble" dir="rtl">
              <p>{t.text}</p>
              {t.response?.proposal && (
                <div className="proposal">
                  <h3>طلب تأكيد — تأكد قبل التنفيذ</h3>
                  <dl>
                    <dt>الأداة</dt>
                    <dd>
                      <code>{t.response.proposal.tool_name}</code>
                    </dd>
                    <dt>وسم العملية</dt>
                    <dd>
                      <code className="hash">{t.response.proposal.operation_hash.slice(0, 16)}…</code>
                    </dd>
                    <dt>صلاحية</dt>
                    <dd>{new Date(t.response.proposal.expires_at).toLocaleString("ar-EG")}</dd>
                  </dl>
                  <pre>{JSON.stringify(t.response.proposal.arguments, null, 2)}</pre>
                  <div className="row">
                    <button
                      className="btn btn-primary"
                      disabled={busy}
                      onClick={() => confirm(t.response!.proposal!.proposal_id)}
                    >
                      تأكيد وتنفيذ
                    </button>
                    <button
                      className="btn btn-ghost"
                      disabled={busy}
                      onClick={() => decline(t.response!.proposal!.proposal_id)}
                    >
                      إلغاء
                    </button>
                  </div>
                </div>
              )}
            </div>
          </li>
        ))}
      </ol>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="اكتب طلبك هنا…"
          rows={2}
          dir="rtl"
          disabled={busy}
        />
        <button className="btn btn-primary" type="submit" disabled={busy || !message.trim()}>
          {busy ? "جارٍ المعالجة…" : "إرسال"}
        </button>
      </form>
    </section>
  );
}

function classifyTurn(r: GatewayResponse): Turn["kind"] {
  if (r.error) return "error";
  if (r.proposal) return "proposal";
  return "assistant";
}
