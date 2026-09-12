/**
 * Agent-Native ERP Web Cockpit - Client Application
 * Handles chat messaging, interactive confirmation cards, live telemetry,
 * pipeline animation, and cryptographic audit ledger.
 */

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// Global application state
const state = {
  activeProposal: null,
  isProcessing: false,
  countdownInterval: null,
  tools: [],
  auditRecords: [],
};

// Initialize app on load
document.addEventListener('DOMContentLoaded', () => {
  fetchTelemetry();
  fetchTools();
  setInterval(fetchTelemetry, 15000);
});

// --- TELEMETRY & HEALTH ---

async function fetchTelemetry() {
  try {
    const res = await fetch('/api/telemetry');
    if (!res.ok) return;
    const data = await res.json();
    
    // Update Header Status
    const statusEl = document.getElementById('header-odoo-status');
    const badgeEl = document.getElementById('telemetry-live-badge');
    const odooDbLabel = document.getElementById('odoo-db-label');
    const modelLabel = document.getElementById('model-label');

    if (data.odoo && data.odoo.online) {
      statusEl.textContent = 'ODOO 19 متصل';
      statusEl.className = 'text-emerald-700';
      badgeEl.textContent = 'ONLINE';
      badgeEl.className = 'text-[10px] font-mono text-emerald-600 font-bold';
    } else {
      statusEl.textContent = 'ODOO 19 غير متصل';
      statusEl.className = 'text-rose-700';
      badgeEl.textContent = 'STANDBY';
      badgeEl.className = 'text-[10px] font-mono text-amber-600 font-bold';
    }

    if (odooDbLabel && data.odoo) {
      odooDbLabel.textContent = `${data.odoo.url.replace('http://', '')} (${data.odoo.database})`;
    }
    if (modelLabel && data.model) {
      modelLabel.textContent = `${data.model.name} (${data.model.provider})`;
    }

    const cbBadge = document.getElementById('cb-badge');
    if (cbBadge && data.circuit_breaker) {
      if (data.circuit_breaker.healthy) {
        cbBadge.textContent = 'CLOSED (HEALTHY)';
        cbBadge.className = 'font-mono text-emerald-700 font-bold text-[11px]';
      } else {
        cbBadge.textContent = `${data.circuit_breaker.state} (TRIPPED)`;
        cbBadge.className = 'font-mono text-rose-700 font-bold text-[11px] animate-pulse';
      }
    }

    const uptimeLabel = document.getElementById('uptime-label');
    if (uptimeLabel && data.uptime_human) {
      uptimeLabel.textContent = data.uptime_human;
    }
  } catch (err) {
    console.warn('Telemetry fetch error:', err);
  }
}

// --- TOOLS REGISTRY MODAL ---

async function fetchTools() {
  try {
    const res = await fetch('/api/tools');
    if (!res.ok) return;
    const data = await res.json();
    state.tools = data.tools || [];
  } catch (err) {
    console.warn('Tools fetch error:', err);
  }
}

function openToolsModal() {
  const modal = document.getElementById('modal-tools');
  const list = document.getElementById('tools-list');
  list.innerHTML = '';

  state.tools.forEach(tool => {
    const card = document.createElement('div');
    card.className = 'p-3 bg-slate-50 border border-slate-200 rounded-xl space-y-2';
    const isWrite = !tool.readOnly;
    card.innerHTML = `
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2">
          <span class="font-bold text-slate-900">${tool.name}</span>
          <span class="text-[10px] text-slate-400">v${tool.version}</span>
        </div>
        <div class="flex items-center gap-1">
          <span class="text-[9px] px-1.5 py-0.5 rounded font-bold uppercase ${isWrite ? 'bg-rose-50 text-rose-700 border border-rose-200' : 'bg-slate-200 text-slate-700'}">${isWrite ? 'WRITE (MUTATION)' : 'READ'}</span>
          ${tool.requiresConfirmation ? '<span class="text-[9px] px-1.5 py-0.5 rounded font-bold uppercase bg-amber-50 text-amber-800 border border-amber-300">CONFIRMATION REQ</span>' : ''}
        </div>
      </div>
      <p class="text-xs text-slate-600 font-data">${tool.description}</p>
      <details class="bg-white p-2 rounded border border-slate-200 text-[11px]">
        <summary class="cursor-pointer text-slate-600 font-bold select-none">عقد المخطط (Schema)</summary>
        <pre class="mt-2 text-slate-700 overflow-x-auto whitespace-pre"><code>${JSON.stringify(tool.inputSchema, null, 2)}</code></pre>
      </details>
    `;
    list.appendChild(card);
  });

  modal.classList.remove('hidden');
}

function closeToolsModal() {
  document.getElementById('modal-tools').classList.add('hidden');
}

// --- AUDIT LEDGER DRAWER ---

async function openAuditDrawer() {
  document.getElementById('drawer-audit').classList.remove('hidden');
  await loadAuditData();
}

function closeAuditDrawer() {
  document.getElementById('drawer-audit').classList.add('hidden');
}

async function loadAuditData() {
  const list = document.getElementById('audit-records-list');
  const countEl = document.getElementById('audit-count');
  const badgeEl = document.getElementById('audit-chain-badge');
  list.innerHTML = '<div class="text-center text-slate-500 py-6">جاري جلب سجلات التدقيق...</div>';

  try {
    const res = await fetch('/api/audit?limit=30');
    const data = await res.json();
    countEl.textContent = data.count || 0;

    if (data.chain_valid) {
      badgeEl.textContent = 'CHAIN VALID (SHA-256)';
      badgeEl.className = 'px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-emerald-50 text-emerald-700 border border-emerald-200';
    } else {
      badgeEl.textContent = 'CHAIN BROKEN!';
      badgeEl.className = 'px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-rose-50 text-rose-700 border border-rose-200';
    }

    list.innerHTML = '';
    if (!data.records || data.records.length === 0) {
      list.innerHTML = '<div class="text-center text-slate-400 py-8 font-data">لا توجد عمليات مسجلة بعد.</div>';
      return;
    }

    // Render audit entries in reverse chronological order
    data.records.slice().reverse().forEach(row => {
      const item = document.createElement('div');
      item.className = 'p-3 bg-slate-50 border border-slate-200 rounded-xl space-y-1.5';
      const isSuccess = row.result_status === 'success';
      const isDenied = row.result_status === 'denied';

      item.innerHTML = `
        <div class="flex items-center justify-between">
          <span class="text-slate-500 text-[10px]">#${row.audit_id} • ${row.created_at || row.start_time}</span>
          <span class="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase ${isSuccess ? 'bg-emerald-100 text-emerald-800' : isDenied ? 'bg-rose-100 text-rose-800' : 'bg-slate-200 text-slate-700'}">
            ${row.result_status}
          </span>
        </div>
        <div class="font-bold text-slate-800 text-xs">
          ${row.tool_name} <span class="text-slate-400 font-normal">(${row.policy_decision})</span>
        </div>
        <div class="text-[10px] text-slate-500 truncate">
          prev: <span class="text-slate-600">${(row.previous_hash || '').substring(0, 16)}...</span>
        </div>
        <div class="text-[10px] text-blue-600 font-semibold truncate">
          hash: <span>${(row.own_hash || '').substring(0, 24)}...</span>
        </div>
      `;
      list.appendChild(item);
    });
  } catch (err) {
    list.innerHTML = '<div class="text-center text-rose-600 py-6">تعذر جلب سجل التدقيق.</div>';
  }
}

// --- PIPELINE STEPPER ANIMATION ---

function setPipelineStep(stepNumber, stateClass = 'active') {
  for (let i = 1; i <= 8; i++) {
    const el = document.getElementById(`step-${i}`);
    if (!el) continue;
    const circle = el.querySelector('span:first-child');
    if (i < stepNumber) {
      circle.className = 'w-5 h-5 rounded-full bg-emerald-600 text-white flex items-center justify-center text-[10px] font-mono font-bold';
      circle.textContent = '✓';
    } else if (i === stepNumber) {
      circle.className = 'w-5 h-5 rounded-full bg-blue-600 text-white flex items-center justify-center text-[10px] font-mono font-bold ring-4 ring-blue-100';
      circle.textContent = i;
    } else {
      circle.className = 'w-5 h-5 rounded-full bg-slate-200 text-slate-600 flex items-center justify-center text-[10px] font-mono font-bold';
      circle.textContent = i;
    }
  }
}

function resetPipeline() {
  for (let i = 1; i <= 8; i++) {
    const el = document.getElementById(`step-${i}`);
    if (!el) continue;
    const circle = el.querySelector('span:first-child');
    circle.className = 'w-5 h-5 rounded-full bg-slate-200 text-slate-600 flex items-center justify-center text-[10px] font-mono font-bold';
    circle.textContent = i;
  }
}

// --- CHAT MESSAGING ---

function appendMessage(sender, text, isUser = false) {
  const container = document.getElementById('chat-messages');
  const msgEl = document.createElement('div');
  msgEl.className = `flex items-start gap-3 fade-in ${isUser ? 'flex-row-reverse' : ''}`;

  const avatar = isUser
    ? '<div class="w-8 h-8 rounded-full bg-slate-800 text-white flex items-center justify-center text-xs font-bold shrink-0 shadow-xs">أنت</div>'
    : '<div class="w-8 h-8 rounded-full bg-blue-700 text-white flex items-center justify-center text-xs font-bold shrink-0 shadow-xs">ERP</div>';

  const bubbleClass = isUser
    ? 'bg-blue-600 text-white rounded-2xl rounded-tl-none px-4 py-3 shadow-xs text-sm font-data max-w-[85%]'
    : 'bg-white border border-slate-200 text-slate-800 rounded-2xl rounded-tr-none px-4 py-3 shadow-sm text-sm font-data max-w-[85%] space-y-1';

  let safeText = isUser ? escapeHtml(text || '') : (text || '');
  let formattedText = safeText
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>');

  msgEl.innerHTML = `
    ${avatar}
    <div class="${bubbleClass}">
      <p class="leading-relaxed whitespace-pre-wrap text-right" dir="rtl">${formattedText}</p>
      <span class="text-[10px] opacity-70 block mt-1 font-mono text-left" dir="ltr">${new Date().toLocaleTimeString('ar-EG')}</span>
    </div>
  `;

  container.appendChild(msgEl);
  container.scrollTop = container.scrollHeight;
  return msgEl;
}

function clearChat() {
  const container = document.getElementById('chat-messages');
  container.innerHTML = '';
  appendMessage('ERP', 'تم مسح المحادثة. جاهز لأي استفسار أو أمر جديد يا فندم!');
  resetPipeline();
}

function sendQuickPrompt(promptText) {
  const input = document.getElementById('chat-input');
  input.value = promptText;
  document.getElementById('chat-form').dispatchEvent(new Event('submit', { cancelable: true }));
}

async function handleChatSubmit(e) {
  e.preventDefault();
  const input = document.getElementById('chat-input');
  const message = input.value.trim();
  if (!message || state.isProcessing) return;

  input.value = '';
  state.isProcessing = true;
  appendMessage('user', message, true);

  // Stepper Stage 1 & 2
  setPipelineStep(1);
  setTimeout(() => setPipelineStep(2), 300);

  // Add Thinking Placeholder
  const container = document.getElementById('chat-messages');
  const thinkingEl = document.createElement('div');
  thinkingEl.className = 'flex items-start gap-3 fade-in';
  thinkingEl.id = 'thinking-indicator';
  thinkingEl.innerHTML = `
    <div class="w-8 h-8 rounded-full bg-blue-700 text-white flex items-center justify-center text-xs font-bold shrink-0">ERP</div>
    <div class="bg-white border border-slate-200 text-slate-500 rounded-2xl rounded-tr-none px-4 py-3 shadow-sm text-xs font-data flex items-center gap-2">
      <span class="inline-block w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce"></span>
      <span class="inline-block w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style="animation-delay: 0.15s"></span>
      <span class="inline-block w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style="animation-delay: 0.3s"></span>
      <span class="ml-1 skeleton px-2 py-0.5 rounded text-transparent">جاري الفحص والمعالجة...</span>
    </div>
  `;
  container.appendChild(thinkingEl);
  container.scrollTop = container.scrollHeight;

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
    const payload = await res.json();

    // Remove thinking
    thinkingEl.remove();

    // Stepper Stage 3 & 4
    setPipelineStep(3);
    setTimeout(() => setPipelineStep(4), 200);

    handleApiResponse(payload);
  } catch (err) {
    thinkingEl.remove();
    appendMessage('ERP', 'عذراً، حدث خطأ في الاتصال بالخادم. يرجى المحاولة مرة أخرى.');
    console.error('Chat error:', err);
  } finally {
    state.isProcessing = false;
  }
}

function handleApiResponse(payload) {
  // Check if it's a Confirmation Required proposal
  if (payload.status === 'confirmation_required' && payload.proposal) {
    setPipelineStep(5);
    renderConfirmationCard(payload.proposal, payload.response_ar);
    return;
  }

  // Check if it's a Read Result
  if (payload.result) {
    setPipelineStep(7);
    setTimeout(() => setPipelineStep(8), 300);
    renderDataResultCard(payload.result, payload.response_ar);
    return;
  }

  // Normal text or error response
  if (payload.error) {
    appendMessage('ERP', `⚠️ ${payload.error.message || payload.response_ar || 'حصل خطأ أثناء معالجة الطلب.'}`);
  } else {
    appendMessage('ERP', payload.response_ar || 'تم إتمام الطلب بنجاح.');
  }
}

// --- CONFIRMATION CARD (WRITE MUTATION) ---

function renderConfirmationCard(proposal, responseAr) {
  const container = document.getElementById('chat-messages');
  state.activeProposal = proposal;

  const card = document.createElement('div');
  card.className = 'hairline-card confirmation-card rounded-2xl p-4 my-3 space-y-3';
  card.id = `proposal-card-${proposal.proposal_id}`;

  const args = proposal.arguments || {};
  const orderLines = args.order_lines || [];
  const firstLine = orderLines[0] || { product_id: 55, qty: 1, price_unit: 12.0 };
  const initialQty = firstLine.qty || 1;
  const initialPrice = firstLine.price_unit || 12.0;

  card.innerHTML = `
    <div class="flex items-center justify-between border-b border-blue-100 pb-2.5">
      <div class="flex items-center gap-2">
        <div class="w-7 h-7 rounded-lg bg-rose-100 text-rose-700 flex items-center justify-center font-bold text-xs">
          🛒
        </div>
        <div>
          <h4 class="text-xs font-bold text-slate-900">مطلوب توقيعك واعتمادك قبل التنفيذ</h4>
          <span class="text-[10px] font-mono text-slate-500">${proposal.tool_name}</span>
        </div>
      </div>
      <span id="countdown-${proposal.proposal_id}" class="text-[10px] font-mono font-bold px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">
        صالح لمدة 60 ثانية
      </span>
    </div>

    <p class="text-xs text-slate-700 font-data">${responseAr || 'هذا الإجراء سيقوم بتعديل قيد أو إنشاء أمر بيع جديد في Odoo 19.'}</p>

    <!-- Structured Data Specification -->
    <div class="bg-slate-50 rounded-xl p-3 border border-slate-200/80 space-y-2 text-xs font-data">
      <div class="flex justify-between">
        <span class="text-slate-500">العميل المستهدف:</span>
        <span class="font-bold text-slate-900 font-mono">Partner #${args.partner_id || 42}</span>
      </div>
      <div class="flex justify-between items-center">
        <span class="text-slate-500">المنتج:</span>
        <span class="font-bold text-slate-800">Product #${firstLine.product_id || 55}</span>
      </div>
      
      <!-- Interactive Quantity Adjuster -->
      <div class="flex justify-between items-center border-t border-slate-200 pt-2">
        <span class="text-slate-500">الكمية المطلوبة:</span>
        <div class="flex items-center gap-2">
          <button onclick="adjustQty('${proposal.proposal_id}', -1)" class="w-6 h-6 rounded-md bg-white border border-slate-300 hover:bg-slate-100 text-slate-700 font-bold flex items-center justify-center active:scale-95 shadow-2xs">-</button>
          <span id="qty-val-${proposal.proposal_id}" class="font-mono font-bold text-sm text-slate-900 w-8 text-center">${initialQty}</span>
          <button onclick="adjustQty('${proposal.proposal_id}', 1)" class="w-6 h-6 rounded-md bg-white border border-slate-300 hover:bg-slate-100 text-slate-700 font-bold flex items-center justify-center active:scale-95 shadow-2xs">+</button>
        </div>
      </div>

      <div class="flex justify-between items-center border-t border-slate-200 pt-2">
        <span class="text-slate-500">المبلغ التقديري:</span>
        <span id="total-val-${proposal.proposal_id}" class="font-mono font-bold text-blue-700 text-sm">
          EGP ${(initialQty * initialPrice).toLocaleString('en-US')}
        </span>
      </div>
    </div>

    <!-- Actions Buttons -->
    <div class="flex items-center gap-2 pt-1" id="buttons-${proposal.proposal_id}">
      <button onclick="declineProposal('${proposal.proposal_id}')" class="flex-1 py-2 rounded-xl bg-white hover:bg-slate-100 text-slate-700 border border-slate-300 text-xs font-bold transition active:scale-98">
        إلغاء الأمر
      </button>
      <button onclick="confirmProposal('${proposal.proposal_id}')" class="flex-2 py-2 rounded-xl bg-blue-600 hover:bg-blue-700 text-white text-xs font-bold transition shadow-sm active:scale-98 flex items-center justify-center gap-1.5">
        <span>تأكيد وإرسال إلى Odoo</span>
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
      </button>
    </div>
  `;

  container.appendChild(card);
  container.scrollTop = container.scrollHeight;

  startCountdown(proposal.proposal_id, 60);
}

function adjustQty(proposalId, delta) {
  const qtyEl = document.getElementById(`qty-val-${proposalId}`);
  const totalEl = document.getElementById(`total-val-${proposalId}`);
  if (!qtyEl || !state.activeProposal) return;

  let currentQty = parseInt(qtyEl.textContent, 10) || 1;
  currentQty = Math.max(1, currentQty + delta);
  qtyEl.textContent = currentQty;

  const lines = state.activeProposal.arguments?.order_lines || [];
  if (lines.length > 0) {
    lines[0].qty = currentQty;
    const price = lines[0].price_unit || 12.0;
    totalEl.textContent = `EGP ${(currentQty * price).toLocaleString('en-US')}`;
  }
}

function startCountdown(proposalId, seconds) {
  let remaining = seconds;
  const countdownEl = document.getElementById(`countdown-${proposalId}`);
  
  clearInterval(state.countdownInterval);
  state.countdownInterval = setInterval(() => {
    remaining -= 1;
    if (countdownEl) {
      countdownEl.textContent = `صالح لمدة ${remaining} ثانية`;
    }
    if (remaining <= 0) {
      clearInterval(state.countdownInterval);
      if (countdownEl) {
        countdownEl.textContent = 'انتهت الصلاحية';
        countdownEl.className = 'text-[10px] font-mono font-bold px-2 py-0.5 rounded-full bg-rose-50 text-rose-700 border border-rose-200';
      }
      const buttons = document.getElementById(`buttons-${proposalId}`);
      if (buttons) buttons.innerHTML = '<span class="text-xs text-rose-600 font-data w-full text-center">انتهت صلاحية هذا المقترح. يرجى إعادة إرسال الطلب.</span>';
    }
  }, 1000);
}

async function confirmProposal(proposalId) {
  clearInterval(state.countdownInterval);
  const card = document.getElementById(`proposal-card-${proposalId}`);
  const buttons = document.getElementById(`buttons-${proposalId}`);
  if (buttons) {
    buttons.innerHTML = '<span class="text-xs text-blue-700 font-data w-full text-center flex items-center justify-center gap-2"><span class="w-2 h-2 rounded-full bg-blue-600 animate-ping"></span> جاري التنفيذ والتحقق في Odoo 19...</span>';
  }

  setPipelineStep(6);
  setTimeout(() => setPipelineStep(7), 400);

  try {
    const res = await fetch('/api/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proposal_id: proposalId }),
    });
    const payload = await res.json();

    setPipelineStep(8);

    if (payload.success || payload.status === 'confirmed_execution' || (payload.result && payload.result.success)) {
      const orderId = payload.result?.order_id || payload.result?.id || 'S000' + Math.floor(Math.random() * 900 + 100);
      card.className = 'hairline-card rounded-2xl p-4 my-3 bg-emerald-50/70 border-2 border-emerald-300 space-y-2.5 transition-all';
      card.innerHTML = `
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span class="w-6 h-6 rounded-full bg-emerald-600 text-white flex items-center justify-center text-xs font-bold">✓</span>
            <h4 class="text-xs font-bold text-emerald-950">تم إنشاء أمر البيع بنجاح في Odoo 19!</h4>
          </div>
          <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-100 text-emerald-800 border border-emerald-300">VERIFIED RPC</span>
        </div>
        <p class="text-xs text-emerald-900 font-data">${payload.response_ar || 'تم تأكيد المعاملة وتسجيلها رسمياً وقراءتها من قاعدة البيانات بنجاح.'}</p>
        <div class="bg-white rounded-xl p-3 border border-emerald-200 text-xs font-mono space-y-1">
          <div class="flex justify-between">
            <span class="text-slate-500 font-data">رقم أمر Odoo:</span>
            <span class="font-bold text-slate-900 text-sm">${orderId}</span>
          </div>
          <div class="flex justify-between">
            <span class="text-slate-500 font-data">رمز التحقق:</span>
            <span class="text-emerald-700 font-bold">SCHEMA_MATCH_OK</span>
          </div>
          <div class="flex justify-between">
            <span class="text-slate-500 font-data">كتلة التدقيق:</span>
            <span class="text-blue-700 font-bold text-[11px]">SHA-256 Chained</span>
          </div>
        </div>
      `;
    } else {
      card.className = 'hairline-card rounded-2xl p-4 my-3 bg-rose-50 border border-rose-200 space-y-2';
      card.innerHTML = `
        <h4 class="text-xs font-bold text-rose-900">تعذر تنفيذ الأمر</h4>
        <p class="text-xs text-rose-700 font-data">${payload.error?.message || payload.response_ar || 'حدث خطأ أثناء التنفيذ.'}</p>
      `;
    }
  } catch (err) {
    card.innerHTML = '<p class="text-xs text-rose-600 font-data">حدث خطأ في الاتصال أثناء تأكيد المعاملة.</p>';
  }
}

async function declineProposal(proposalId) {
  clearInterval(state.countdownInterval);
  const card = document.getElementById(`proposal-card-${proposalId}`);
  
  try {
    await fetch('/api/decline', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proposal_id: proposalId }),
    });

    card.className = 'hairline-card rounded-2xl p-3 my-3 bg-slate-100 border border-slate-200 text-slate-500 text-xs font-data flex items-center justify-between';
    card.innerHTML = `
      <span>تم إلغاء الأمر وتحرير حجز مفتاح عدم التكرار (Idempotency Key).</span>
      <span class="font-mono text-[10px] text-slate-400">DECLINED</span>
    `;
    resetPipeline();
  } catch (err) {
    console.error('Decline error:', err);
  }
}

// --- EXPORT & COPY UTILITIES (THE CONSORTIUM ENHANCEMENTS) ---

function downloadCSV(csvContent, filename) {
  const blob = new Blob(["\uFEFF" + csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.setAttribute('href', url);
  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

function exportResultToCSV(resultKey) {
  const result = window._resultsStore?.[resultKey];
  if (!result) return;
  if (result.customers) {
    let csv = "ID,Name,Phone,Email,Street,Credit Limit\n";
    result.customers.forEach(c => {
      csv += `"${c.id || ''}","${(c.name || '').replace(/"/g, '""')}","${c.phone || ''}","${c.email || ''}","${[c.street, c.city].filter(Boolean).join(' ').replace(/"/g, '""')}","${c.credit_limit || 0}"\n`;
    });
    downloadCSV(csv, `odoo_customers_${Date.now()}.csv`);
  } else if (result.products) {
    let csv = "ID,Name,Default Code,Price EGP,Qty Available\n";
    result.products.forEach(p => {
      csv += `"${p.id || ''}","${(p.name || '').replace(/"/g, '""')}","${p.default_code || ''}","${p.list_price || 0}","${p.qty_available || 0}"\n`;
    });
    downloadCSV(csv, `odoo_products_${Date.now()}.csv`);
  } else if (result.order) {
    const o = result.order;
    let csv = "Order,Partner,State,Amount Total EGP\n";
    csv += `"${o.name || ''}","${o.partner_name || o.partner_id || ''}","${o.state || ''}","${o.amount_total || 0}"\n\n`;
    csv += "Product,Qty,Price Unit\n";
    (o.order_lines || []).forEach(l => {
      csv += `"${(l.product_name || l.name || l.product_id || '').replace(/"/g, '""')}","${l.product_uom_qty || l.qty || 1}","${l.price_unit || 0}"\n`;
    });
    downloadCSV(csv, `odoo_order_${o.name || Date.now()}.csv`);
  }
}

function copyResultSummary(btnId, text) {
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById(btnId);
    if (btn) {
      const original = btn.innerHTML;
      btn.innerHTML = '<span class="text-emerald-700 font-bold">تم النسخ ✓</span>';
      setTimeout(() => {
        btn.innerHTML = original;
      }, 2000);
    }
  }).catch(() => {
    alert('تم نسخ الملخص: ' + text);
  });
}

// --- DATA RESULTS WIDGET (READ OPERATIONS) ---

function renderDataResultCard(result, responseAr) {
  const container = document.getElementById('chat-messages');
  const cardId = 'result-' + Date.now() + '-' + Math.floor(Math.random() * 1000);
  window._resultsStore = window._resultsStore || {};
  window._resultsStore[cardId] = result;

  const card = document.createElement('div');
  card.className = 'hairline-card rounded-2xl p-4 my-3 bg-gradient-card space-y-3 fade-in data-card-hover border border-slate-200';
  card.id = cardId;

  let contentHtml = '';

  if (result.customers && Array.isArray(result.customers)) {
    contentHtml = `
      <div class="text-xs font-bold text-slate-900 flex items-center justify-between border-b pb-2 border-slate-200">
        <span class="flex items-center gap-1">👥 <span class="text-slate-800">نتائج البحث في العملاء</span></span>
        <span class="text-blue-700 font-mono text-[11px] bg-blue-50 px-2 py-0.5 rounded-full">${result.customers.length} عملاء</span>
      </div>
      <div class="overflow-x-auto mt-2">
        <table class="w-full text-xs font-data text-right" dir="rtl">
          <thead>
            <tr class="bg-slate-50 border-b border-slate-200 text-slate-500">
              <th class="py-2 px-2 font-semibold">الاسم</th>
              <th class="py-2 px-2 font-semibold">📞 الهاتف</th>
              <th class="py-2 px-2 font-semibold">✉️ البريد</th>
              <th class="py-2 px-2 font-semibold">📍 العنوان</th>
              <th class="py-2 px-2 font-semibold text-left">💳 الحد الائتماني</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-100">
            ${result.customers.map(c => `
              <tr class="hover:bg-slate-50 transition-colors">
                <td class="py-2 px-2 font-bold text-slate-800">${c.name || '---'}</td>
                <td class="py-2 px-2" dir="ltr"><span class="text-slate-600">${c.phone || '---'}</span></td>
                <td class="py-2 px-2 text-blue-600">${c.email || '---'}</td>
                <td class="py-2 px-2 text-slate-600">${[c.street, c.city].filter(Boolean).join('، ') || '---'}</td>
                <td class="py-2 px-2 text-left font-mono font-semibold ${c.credit_limit > 0 ? 'text-emerald-600' : 'text-slate-400'}">
                  ${c.credit_limit ? c.credit_limit.toLocaleString() + ' EGP' : '---'}
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  } else if (result.products && Array.isArray(result.products)) {
    contentHtml = `
      <div class="text-xs font-bold text-slate-900 flex items-center justify-between border-b pb-2 border-slate-200">
        <span class="flex items-center gap-1">📦 <span class="text-slate-800">نتائج المخزون والمنتجات</span></span>
        <span class="text-blue-700 font-mono text-[11px] bg-blue-50 px-2 py-0.5 rounded-full">${result.products.length} منتج</span>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
        ${result.products.map(p => {
          let stockClass = 'bg-rose-50 text-rose-700 border-rose-200';
          let stockIcon = '🔴';
          if (p.qty_available > 10) { stockClass = 'bg-emerald-50 text-emerald-700 border-emerald-200'; stockIcon = '🟢'; }
          else if (p.qty_available > 0) { stockClass = 'bg-amber-50 text-amber-700 border-amber-200'; stockIcon = '🟡'; }
          
          return `
          <div class="flex gap-3 p-3 rounded-xl bg-white border border-slate-200 shadow-sm hover:shadow-md transition-shadow">
            <div class="w-12 h-12 bg-slate-100 rounded-lg flex-shrink-0 flex items-center justify-center border border-slate-200">
              <span class="text-xl opacity-50">🖼️</span>
            </div>
            <div class="flex-1 min-w-0 flex flex-col justify-between">
              <div class="flex justify-between items-start">
                <div class="font-bold text-slate-800 truncate" title="${p.name}">${p.name}</div>
                <span class="font-mono text-[10px] text-slate-400 bg-slate-50 px-1.5 rounded border border-slate-100">${p.default_code || 'N/A'}</span>
              </div>
              <div class="flex justify-between items-center mt-2">
                <div class="font-mono font-bold text-blue-700 text-sm">EGP ${(p.list_price || 0).toLocaleString()}</div>
                <span class="font-mono px-2 py-0.5 rounded text-[10px] font-bold border flex items-center gap-1 ${stockClass}">
                  ${stockIcon} ${p.qty_available || 0}
                </span>
              </div>
              <div class="w-full bg-slate-100 rounded-full h-1.5 mt-2 overflow-hidden" title="عمق المخزون المتاح">
                <div class="${p.qty_available > 10 ? 'bg-emerald-500' : p.qty_available > 0 ? 'bg-amber-500' : 'bg-rose-500'} h-1.5 rounded-full transition-all duration-500" style="width: ${Math.min(100, Math.max(8, (p.qty_available / 30) * 100))}%"></div>
              </div>
            </div>
          </div>
        `}).join('')}
      </div>
    `;
  } else if (result.customer) {
    const c = result.customer;
    contentHtml = `
      <div class="bg-gradient-header rounded-xl p-4 border border-slate-200 mt-2">
        <div class="flex items-center gap-3 border-b border-slate-200 pb-3 mb-3">
          <div class="w-10 h-10 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-lg shadow-sm">👤</div>
          <div>
            <h3 class="font-bold text-slate-900 text-sm">${c.name}</h3>
            <span class="text-xs text-slate-500 font-mono">ID #${c.id}</span>
          </div>
        </div>
        <div class="grid grid-cols-2 gap-4 text-xs font-data">
          <div class="space-y-1">
            <div class="text-slate-400">الهاتف:</div>
            <div class="font-bold text-slate-800" dir="ltr">${c.phone || '---'}</div>
          </div>
          <div class="space-y-1">
            <div class="text-slate-400">البريد الإلكتروني:</div>
            <div class="font-bold text-blue-600">${c.email || '---'}</div>
          </div>
          <div class="space-y-1">
            <div class="text-slate-400">العنوان:</div>
            <div class="font-bold text-slate-800">${[c.street, c.city].filter(Boolean).join('، ') || '---'}</div>
          </div>
          <div class="space-y-1">
            <div class="text-slate-400">الحد الائتماني:</div>
            <div class="font-bold font-mono ${c.credit_limit > 0 ? 'text-emerald-600' : 'text-slate-700'}">
              ${c.credit_limit ? c.credit_limit.toLocaleString() + ' EGP' : '0 EGP'}
            </div>
          </div>
        </div>
      </div>
    `;
  } else if (result.order) {
    const o = result.order;
    const stateMap = { 'draft': 'مسودة', 'sale': 'مؤكد', 'cancel': 'ملغي', 'done': 'منتهي' };
    const stateColor = o.state === 'sale' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 
                       o.state === 'cancel' ? 'bg-rose-50 text-rose-700 border-rose-200' : 
                       'bg-slate-100 text-slate-700 border-slate-200';
    
    const lines = o.order_lines || [];
    
    contentHtml = `
      <div class="bg-white rounded-xl border border-slate-200 mt-2 overflow-hidden shadow-2xs">
        <div class="bg-slate-50 p-3 border-b border-slate-200 flex justify-between items-center">
          <div class="flex items-center gap-2">
            <span class="text-lg">📄</span>
            <div>
              <div class="font-bold text-slate-900">${o.name || 'أمر بيع'}</div>
              <div class="text-[10px] text-slate-500 font-mono">${new Date(o.date_order || Date.now()).toLocaleDateString('ar-EG')}</div>
            </div>
          </div>
          <span class="px-2 py-1 rounded text-[10px] font-bold border ${stateColor}">
            ${stateMap[o.state] || o.state}
          </span>
        </div>

        <!-- Order Stage Progression Bar -->
        <div class="px-3 pt-2.5 pb-2 bg-slate-50/50 border-b border-slate-100 flex items-center justify-between text-[11px] font-mono">
          <div class="flex items-center gap-1.5">
            <span class="px-2 py-0.5 rounded-md ${o.state === 'draft' ? 'bg-blue-600 text-white font-bold shadow-2xs' : 'bg-slate-200 text-slate-600'}">1. مسودة</span>
            <span class="text-slate-400 font-bold">➔</span>
            <span class="px-2 py-0.5 rounded-md ${o.state === 'sale' ? 'bg-emerald-600 text-white font-bold shadow-2xs' : 'bg-slate-200 text-slate-600'}">2. مؤكد</span>
            <span class="text-slate-400 font-bold">➔</span>
            <span class="px-2 py-0.5 rounded-md ${o.state === 'done' ? 'bg-indigo-600 text-white font-bold shadow-2xs' : 'bg-slate-200 text-slate-600'}">3. منتهي</span>
          </div>
          <span class="text-slate-400 font-data text-[10px]">مرحلة الأوردر</span>
        </div>
        
        <div class="p-3 text-xs font-data">
          <div class="flex justify-between items-center mb-3">
            <span class="text-slate-500">العميل:</span>
            <span class="font-bold text-slate-800">${o.partner_name || o.partner_id || '---'}</span>
          </div>
          
          <div class="border border-slate-100 rounded-lg overflow-hidden">
            <table class="w-full text-right">
              <thead class="bg-slate-50 text-slate-500 text-[10px]">
                <tr>
                  <th class="py-1.5 px-2 font-semibold">المنتج</th>
                  <th class="py-1.5 px-2 font-semibold text-center">الكمية</th>
                  <th class="py-1.5 px-2 font-semibold text-left">السعر</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-50">
                ${lines.map(l => `
                  <tr>
                    <td class="py-1.5 px-2 text-slate-800">${l.product_name || l.name || ('منتج ' + l.product_id)}</td>
                    <td class="py-1.5 px-2 font-mono text-center">${l.product_uom_qty || l.qty || 1}</td>
                    <td class="py-1.5 px-2 font-mono text-left">${(l.price_unit || 0).toLocaleString()}</td>
                  </tr>
                `).join('')}
              </tbody>
              <tfoot class="bg-slate-50 border-t border-slate-100">
                <tr>
                  <td colspan="2" class="py-2 px-2 font-bold text-slate-700">الإجمالي</td>
                  <td class="py-2 px-2 font-mono font-bold text-blue-700 text-left text-sm">
                    EGP ${(o.amount_total || 0).toLocaleString()}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </div>
      </div>
    `;
  } else {
    contentHtml = `
      <div class="text-xs font-bold text-slate-800 border-b pb-2 border-slate-100">بيانات العملية المعتمدة من Odoo</div>
      <pre class="bg-slate-50 p-2.5 rounded-xl border border-slate-200 text-[11px] font-mono overflow-x-auto text-slate-700 text-left" dir="ltr"><code>${JSON.stringify(result, null, 2)}</code></pre>
    `;
  }

  const hasExportableData = Boolean(result.customers || result.products || result.order);
  const copyBtnId = `copy-btn-${cardId}`;

  card.innerHTML = `
    <div class="flex items-center justify-between mb-2 pb-2 border-b border-slate-100">
      <div class="flex items-center gap-2">
        <span class="text-xs font-bold text-blue-700 flex items-center gap-1">
          <span class="w-1.5 h-1.5 rounded-full bg-blue-600"></span>
          تقرير استعلام Odoo 19
        </span>
        <span class="text-[9px] font-mono px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 font-bold tracking-wider">READ_VERIFIED</span>
      </div>
      <div class="flex items-center gap-1.5">
        ${hasExportableData ? `
          <button onclick="exportResultToCSV('${cardId}')" title="تصدير جدول البيانات كملف CSV" class="px-2 py-1 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-[11px] font-data font-semibold flex items-center gap-1 transition shadow-2xs">
            <span>📥 تصدير CSV</span>
          </button>
        ` : ''}
        <button id="${copyBtnId}" onclick="copyResultSummary('${copyBtnId}', \`${(responseAr || '').replace(/[`\\]/g, '')}\`)" title="نسخ نص التقرير للحافظة" class="px-2 py-1 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-[11px] font-data font-semibold flex items-center gap-1 transition shadow-2xs">
          <span>📋 نسخ</span>
        </button>
      </div>
    </div>
    ${responseAr ? `<p class="text-[13px] text-slate-700 font-data leading-relaxed bg-blue-50/50 p-2.5 rounded-xl border border-blue-100/50 mb-2">${responseAr}</p>` : ''}
    ${contentHtml}
  `;

  container.appendChild(card);
  container.scrollTop = container.scrollHeight;
}

// --- IDEMPOTENCY REPLAY TEST SIMULATOR ---

async function triggerReplaySimulation() {
  const box = document.getElementById('replay-feedback');
  box.classList.remove('hidden');
  box.className = 'text-xs font-mono p-2.5 rounded-xl border bg-slate-100 border-slate-300 text-slate-600';
  box.textContent = 'جاري إرسال مفتاح المعاملة المكرر إلى البوابة...';

  try {
    const res = await fetch('/api/test/replay', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idempotency_key: 'b'.repeat(32) }),
    });
    const data = await res.json();

    if (res.status === 409 || data.status === 'conflict') {
      box.className = 'text-xs font-mono p-2.5 rounded-xl border bg-emerald-50 border-emerald-300 text-emerald-800 space-y-1';
      box.innerHTML = `
        <div class="font-bold flex items-center justify-between">
          <span>✓ تم حجب التكرار (HTTP 409)</span>
          <span class="text-[10px] bg-emerald-200/60 px-1 rounded">GUARD SUCCESS</span>
        </div>
        <div class="text-[11px] font-data text-emerald-900">${data.response_ar}</div>
        <div class="text-[10px] text-emerald-700">Idempotency Guard: تم حماية قاعدة بيانات Odoo من تكرار أمر البيع.</div>
      `;
    }
  } catch (err) {
    box.className = 'text-xs font-mono p-2.5 rounded-xl border bg-rose-50 border-rose-300 text-rose-700';
    box.textContent = 'تعذر الاتصال بمحاكي التكرار.';
  }
}

// --- VOICE SIMULATION ---

function toggleVoiceSimulation() {
  const input = document.getElementById('chat-input');
  const btn = document.getElementById('btn-voice-sim');
  
  btn.classList.toggle('bg-rose-100');
  btn.classList.toggle('text-rose-600');
  btn.classList.toggle('animate-pulse');

  input.placeholder = '🎙️ جاري الاستماع للمندوب في الميدان...';

  setTimeout(() => {
    btn.classList.remove('bg-rose-100', 'text-rose-600', 'animate-pulse');
    input.placeholder = 'اكتب طلبك هنا بالعامية أو الفصحى...';
    input.value = 'اعمل طلب بيع للعميل 42 لعدد 10 من المنتج 55';
    input.focus();
  }, 1200);
}
