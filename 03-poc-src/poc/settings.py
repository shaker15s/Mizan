"""Runtime settings registry for the Mizan cockpit and the eval harness.

A single, typed, validated source of truth for everything an operator is
allowed to control at runtime: model/provider wiring, agent behaviour,
governance timeouts, UI preferences, and integration credentials.

Design rules (mirrors the security posture of the rest of the POC):

- **Typed + validated.** Every key has a spec (type, bounds, choices). An
  unknown key or an out-of-range value is rejected, never silently accepted.
- **Secrets never touch disk.** ``secret=True`` values live in process memory
  only; snapshots mask them to ``••••last4`` so they cannot leak through
  ``/api/settings``.
- **Environment first.** ``.env`` supplies the defaults; a persisted overlay
  in ``data/settings.json`` (gitignored) holds operator overrides.
- **Versioned.** ``bump`` on every accepted write so caches (LLM client,
  telemetry, UI) can invalidate themselves cheaply.
- **Bounded history.** The last 50 mutations are kept for accountability
  (who changed what, when) without any PII.

This module imports nothing else from ``poc`` (composition-root helper).
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

SRC_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = SRC_ROOT / "data" / "settings.json"
MAX_HISTORY = 50

TYPE_STR = "str"
TYPE_INT = "int"
TYPE_FLOAT = "float"
TYPE_BOOL = "bool"
TYPE_CHOICE = "choice"
TYPE_SECRET = "secret"
TYPE_DICT = "dict"


@dataclass(frozen=True)
class SettingSpec:
    """One operator-controllable setting and how to validate/render it."""

    key: str
    group: str
    label_ar: str
    type: str
    default: Any
    help_ar: str = ""
    choices: tuple[str, ...] = ()
    min_value: float | None = None
    max_value: float | None = None
    env_var: str | None = None
    secret: bool = False
    restart_required: bool = False
    control: str = ""
    tags: tuple[str, ...] = ()

    @property
    def ui_control(self) -> str:
        if self.control:
            return self.control
        if self.secret:
            return "password"
        if self.type == TYPE_BOOL:
            return "toggle"
        if self.type == TYPE_CHOICE:
            return "select"
        if self.type in {TYPE_INT, TYPE_FLOAT}:
            return "number"
        if self.type == TYPE_DICT:
            return "json"
        return "text"


_GROUP_LABELS: dict[str, tuple[str, str]] = {
    # group -> (Arabic label, emoji)
    "model": ("النموذج والمزوّد", "🧠"),
    "agent": ("سلوك المساعد", "🤖"),
    "governance": ("الحوكمة والتأكيدات", "🛡️"),
    "erp": ("الاتصال بـ Odoo", "🔌"),
    "ui": ("الواجهة والتجربة", "🎨"),
    "features": ("المزايا التجريبية", "🧪"),
    "integrations": ("التكاملات الخارجية", "📡"),
}


def _s(key: str, group: str, label: str, default: Any, **kw: Any) -> SettingSpec:
    return SettingSpec(key=key, group=group, label_ar=label, default=default, type=kw.pop("type", TYPE_STR), **kw)


_SPECS: tuple[SettingSpec, ...] = (
    # --- model / provider -----------------------------------------------------
    _s("model.provider", "model", "مزوّد النموذج", "anthropic", type=TYPE_CHOICE,
       choices=("anthropic", "openai_compatible"), env_var="LLM_PROVIDER",
       help_ar="anthropic (SDK رسمي) أو openai_compatible (أي endpoint بواجهة OpenAI)."),
    _s("model.name", "model", "معرّف النموذج", "claude-sonnet-4-5-20250929", env_var="POC_LLM_MODEL",
       help_ar="النموذج المستخدم في المحادثة وفي تقييم --mode live."),
    _s("model.base_url", "model", "عنوان الـ Endpoint", "", env_var="ANTHROPIC_BASE_URL",
       help_ar="اختياري: وسيط متوافق مع Anthropic أو LLM_BASE_URL لمزوّد OpenAI-compatible."),
    _s("model.api_key", "model", "مفتاح الـ API", "", type=TYPE_SECRET, env_var="LLM_API_KEY",
       help_ar="يُخزَّن في ذاكرة العملية فقط ولا يُكتب أبدًا على القرص."),
    _s("model.max_tokens", "model", "حد أقصى للـ tokens", 2048, type=TYPE_INT, min_value=256, max_value=16384,
       help_ar="كل ما قلّ، زادت سرعة الرد. 2048 كافية لرد عربي مُنسّق."),
    _s("model.temperature", "model", "درجة العشوائية", 0.2, type=TYPE_FLOAT, min_value=0.0, max_value=1.0,
       help_ar="منخفضة = إجابات ثابتة وقابلة للتدقيق. ارفعها فقط للجرأة اللغوية."),
    _s("model.timeout_seconds", "model", "مهلة استدعاء النموذج (ثانية)", 60.0, type=TYPE_FLOAT,
       min_value=5.0, max_value=300.0),
    _s("model.retries", "model", "إعادة المحاولة عند الفشل المؤقت", 2, type=TYPE_INT, min_value=0, max_value=5,
       help_ar="Backoff أُسّي مع jitter لأخطاء 429/5xx/الشبكة."),
    _s("model.force_tool_choice", "model", "إجبار استدعاء الأداة", "off", type=TYPE_CHOICE,
       choices=("off", "any", "required"), env_var="FORCE_TOOL_CHOICE",
       help_ar="off = النموذج يقرر. any/required يلغيان تردّد text_only في أوامر الكتابة."),
    # --- agent behaviour ------------------------------------------------------
    _s("agent.response_style", "agent", "أسلوب الرد", "balanced", type=TYPE_CHOICE,
       choices=("concise", "balanced", "detailed"),
       help_ar="مقدار الشرح والتوصيات داخل الكارت الواحد."),
    _s("agent.dialect", "agent", "اللهجة", "ar-EG", type=TYPE_CHOICE, choices=("ar-EG", "ar-MSA", "bilingual"),
       help_ar="مصري مهني / فصحى / مزيج مصطلحات إنجليزية للتقارير."),
    _s("agent.memory_turns", "agent", "عمق ذاكرة الجلسة", 8, type=TYPE_INT, min_value=0, max_value=24,
       help_ar="عدد أزواج الرسائل التي تُرسل مع كل طلب (0 = بلا ذاكرة)."),
    _s("agent.narrative_composer", "agent", "صياغة تحليلية بعد التنفيذ", True, type=TYPE_BOOL,
       help_ar="استدعاء ثانٍ للنموذج يحوّل نتيجة البوابة إلى تحليل مالي/تشغيلي مُبثّل على الطائر."),
    _s("agent.max_input_chars", "agent", "أقصى طول للرسالة", 2000, type=TYPE_INT, min_value=200, max_value=8000),
    _s("agent.slash_commands", "agent", "أوامر \/ المباشرة", True, type=TYPE_BOOL,
       help_ar="/customer محمد — تنفيذ فوري عبر البوابة بدون استدعاء نموذج (صفر تكلفة، صفر انتظار)."),
    # --- governance -----------------------------------------------------------
    _s("governance.confirm_ttl_seconds", "governance", "صلاحية التأكيد (ثانية)", 300, type=TYPE_INT,
       min_value=30, max_value=1800, help_ar="مدة بقاء مقترح الكتابة صالحًا للتوقيع البشري."),
    _s("governance.max_tool_calls", "governance", "أقصى سلسلة استدعاءات", 4, type=TYPE_INT, min_value=1, max_value=10,
       help_ar="حدّ الردّ التدرّجي (fallback ladder) لتسوية أسماء العملاء."),
    _s("governance.audit_page_size", "governance", "حجم صفحة سجل التدقيق", 50, type=TYPE_INT, min_value=10, max_value=500),
    _s("governance.idempotency_window_days", "governance", "نافذة منع التكرار (يوم)", 7, type=TYPE_INT,
       min_value=1, max_value=90),
    # --- erp ------------------------------------------------------------------
    _s("erp.base_url", "erp", "عنوان Odoo", "http://localhost:8069", env_var="ODOO_URL"),
    _s("erp.database", "erp", "قاعدة البيانات", "poc_test", env_var="ODOO_DATABASE"),
    _s("erp.timeout_seconds", "erp", "مهلة ERP (ثانية)", 10.0, type=TYPE_FLOAT, min_value=1.0, max_value=120.0),
    _s("erp.breaker_failure_threshold", "erp", "عتبة قاطع الدائرة", 4, type=TYPE_INT, min_value=1, max_value=20),
    _s("erp.breaker_recovery_seconds", "erp", "زمن الاستعادة (ثانية)", 30.0, type=TYPE_FLOAT, min_value=5.0, max_value=600.0),
    # --- ui -------------------------------------------------------------------
    _s("ui.theme", "ui", "السمة", "light", type=TYPE_CHOICE, choices=("light", "dark", "system")),
    _s("ui.density", "ui", "كثافة الواجهة", "comfortable", type=TYPE_CHOICE, choices=("comfortable", "compact")),
    _s("ui.animations", "ui", "الحركة والانتقالات", True, type=TYPE_BOOL,
       help_ar="تُحترم تلقائيًا مع prefers-reduced-motion."),
    _s("ui.telemetry_interval_seconds", "ui", "تحديث التليمتري (ثانية)", 15, type=TYPE_INT, min_value=3, max_value=300),
    _s("ui.stream_responses", "ui", "بث الرد على الطائر (SSE)", True, type=TYPE_BOOL),
    _s("ui.voice_input", "ui", "الإدخال الصوتي", True, type=TYPE_BOOL),
    _s("ui.show_pipeline", "ui", "شريط مسار الأمان", True, type=TYPE_BOOL),
    _s("ui.replay_simulator", "ui", "محاكي هجوم التكرار", True, type=TYPE_BOOL),
    # --- experimental ---------------------------------------------------------
    _s("features.simulated_llm", "features", "محاكاة النموذج (بدون مفاتيح)", False, type=TYPE_BOOL,
       help_ar="محرك قواعد عربي محلي ينفّذ نفس المسار الكامل حين لا يتوفر مفتاح API — للعرض والـ demo."),
    _s("features.verbose_errors", "features", "تفاصيل الأخطاء للمطوّر", False, type=TYPE_BOOL),
    _s("features.outbound_notifications", "features", "إشعارات ما بعد التنفيذ", False, type=TYPE_BOOL,
       help_ar="إرسال ملخص العملية إلى Slack/WhatsApp/Email/Webhook بعد نجاح الكتابة."),
    # --- integrations (nested config; secrets still memory-only) --------------
    _s("integrations.config", "integrations", "إعدادات التكاملات", {}, type=TYPE_DICT,
       help_ar="مفاتيح الربط لكل تكامل (webhook، slack، whatsapp، smtp)."),
)

SPEC_BY_KEY: dict[str, SettingSpec] = {spec.key: spec for spec in _SPECS}


def all_specs() -> tuple[SettingSpec, ...]:
    return _SPECS


def group_labels() -> dict[str, tuple[str, str]]:
    return dict(_GROUP_LABELS)


class SettingsValidationError(ValueError):
    """Raised for an unknown key or an out-of-range/incorrectly typed value."""

    def __init__(self, errors: Mapping[str, str]) -> None:
        self.errors = dict(errors)
        joined = "; ".join(f"{key}: {msg}" for key, msg in errors.items())
        super().__init__(joined or "invalid settings")


def _env_default(spec: SettingSpec) -> Any:
    if not spec.env_var:
        return None
    raw = os.environ.get(spec.env_var)
    if raw is None or raw == "":
        return None
    if spec.type == TYPE_BOOL:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if spec.type == TYPE_INT:
        try:
            return int(raw)
        except ValueError:
            return None
    if spec.type == TYPE_FLOAT:
        try:
            return float(raw)
        except ValueError:
            return None
    return raw


def _coerce(spec: SettingSpec, value: Any) -> Any:
    """Validate and normalize one value against its spec; raise ValueError."""
    if value is None:
        return spec.default
    if spec.type == TYPE_BOOL:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            token = value.strip().lower()
            if token in {"1", "true", "yes", "on", "صحيح", "نعم"}:
                return True
            if token in {"0", "false", "no", "off", "باطل", "لأ"}:
                return False
        raise ValueError("قيمة منطقية مطلوبة (true/false)")
    if spec.type in {TYPE_INT, TYPE_FLOAT}:
        try:
            number = int(value) if spec.type == TYPE_INT else float(value)
        except (TypeError, ValueError):
            raise ValueError("رقم مطلوب") from None
        if spec.min_value is not None and number < spec.min_value:
            raise ValueError(f"الحد الأدنى {spec.min_value}")
        if spec.max_value is not None and number > spec.max_value:
            raise ValueError(f"الحد الأقصى {spec.max_value}")
        return number
    if spec.type == TYPE_DICT:
        if isinstance(value, str):
            try:
                value = json.loads(value or "{}")
            except json.JSONDecodeError:
                raise ValueError("JSON غير صالح") from None
        if not isinstance(value, dict):
            raise ValueError("كائن JSON مطلوب")
        return value
    if not isinstance(value, str):
        raise ValueError("نص مطلوب")
    text = value.strip() if spec.type != TYPE_SECRET else value
    if spec.type == TYPE_CHOICE and text and text not in spec.choices:
        raise ValueError(f"قيمة من {list(spec.choices)} مطلوبة")
    return text


def mask_secret(value: Any) -> str:
    """Display-safe representation for a secret (never the raw value)."""
    if not value:
        return ""
    text = str(value)
    tail = text[-4:] if len(text) > 8 else ""
    return f"••••••••{tail}" if tail else "••••••••"


@dataclass
class SettingsStore:
    """Validated, versioned runtime configuration with optional persistence."""

    path: Path = SETTINGS_PATH
    persist_secrets: bool = False
    _values: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _secrets: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _version: int = field(default=0, init=False, repr=False)
    _history: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _listeners: list[Callable[["SettingsStore", tuple[str, ...]], None]] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        with self._lock:
            self._values = {}
            for spec in _SPECS:
                env_value = _env_default(spec)
                self._values[spec.key] = spec.default if env_value is None else env_value
            self._load_overlay()

    # --- persistence --------------------------------------------------------
    def _load_overlay(self) -> None:
        if not self.path.exists():
            return
        try:
            overlay = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(overlay, dict):
            return
        values = overlay.get("values") if isinstance(overlay.get("values"), dict) else overlay
        history = overlay.get("history")
        if isinstance(history, list):
            self._history = [row for row in history if isinstance(row, dict)][:MAX_HISTORY]
        errors: dict[str, str] = {}
        for key, value in values.items():
            spec = SPEC_BY_KEY.get(key)
            if spec is None or spec.secret:
                continue
            try:
                self._values[key] = _coerce(spec, value)
            except ValueError as err:
                errors[key] = str(err)
        if errors:  # a stale overlay must never brick the cockpit
            self._history.append({"ts": int(time.time()), "action": "overlay_rejected", "detail": errors})

    def _persist(self) -> None:
        if not self.path.parent.exists():
            return
        payload = {
            "version": self._version,
            "values": {
                key: value
                for key, value in self._values.items()
                if not SPEC_BY_KEY[key].secret or self.persist_secrets
            },
            "history": self._history[-MAX_HISTORY:],
        }
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    # --- reading ------------------------------------------------------------
    @property
    def version(self) -> int:
        return self._version

    def get(self, key: str, default: Any = None) -> Any:
        spec = SPEC_BY_KEY.get(key)
        if spec is None:
            return default if default is not None else self._values.get(key, default)
        return self._values.get(key, spec.default)

    def bool_of(self, key: str) -> bool:
        return bool(self.get(key, False))

    def int_of(self, key: str) -> int:
        return int(self.get(key, 0) or 0)

    def float_of(self, key: str) -> float:
        return float(self.get(key, 0.0) or 0.0)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)

    def secret(self, key: str) -> str:
        spec = SPEC_BY_KEY.get(key)
        if spec is not None and spec.secret:
            return self._secrets.get(key, "") or str(_env_default(spec) or "")
        return str(self._values.get(key, ""))

    # --- writing ------------------------------------------------------------
    def update(self, changes: Mapping[str, Any], *, actor: str = "operator") -> tuple[dict[str, Any], dict[str, str]]:
        """Apply a partial update. Returns (changed_values, errors) and bumps the version.

        Unknown keys are reported as errors rather than dropped, so the UI can
        show the operator exactly what was refused.
        """
        errors: dict[str, str] = {}
        staged: dict[str, Any] = {}
        staged_secrets: dict[str, str] = {}
        with self._lock:
            for key, value in changes.items():
                spec = SPEC_BY_KEY.get(key)
                if spec is None:
                    errors[key] = "إعداد غير معروف"
                    continue
                try:
                    coerced = _coerce(spec, value)
                except ValueError as err:
                    errors[key] = str(err)
                    continue
                if spec.secret:
                    if value in ("", None):
                        continue  # clearing a secret is a no-op; explicit reset below
                    staged_secrets[key] = coerced
                    continue
                if self._values.get(key) != coerced:
                    staged[key] = coerced
            if errors and not staged and not staged_secrets:
                return {}, errors
            self._values.update(staged)
            self._secrets.update(staged_secrets)
            self._version += 1
            record = {
                "ts": int(time.time()),
                "actor": actor,
                "changed": sorted(staged) + [f"{k} (مفتاح سرّي)" for k in staged_secrets],
                "version": self._version,
            }
            self._history.append(record)
            self._history = self._history[-MAX_HISTORY:]
            self._persist()
            changed_all = {**staged, **{k: mask_secret(v) for k, v in staged_secrets.items()}}
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(self, tuple(list(staged) + list(staged_secrets)))
            except Exception:  # a misbehaving listener must never break the write
                pass
        return changed_all, errors

    def reset(self, keys: Iterable[str] | None = None, *, actor: str = "operator") -> dict[str, Any]:
        with self._lock:
            targets = [keys] if isinstance(keys, str) else list(keys or SPEC_BY_KEY.keys())
            restored: dict[str, Any] = {}
            for key in targets:
                spec = SPEC_BY_KEY.get(key)
                if spec is None:
                    continue
                if spec.secret:
                    self._secrets.pop(key, None)
                    continue
                self._values[key] = spec.default
                restored[key] = spec.default
            self._version += 1
            self._history.append({"ts": int(time.time()), "actor": actor, "changed": [f"reset:{k}" for k in targets]})
            self._history = self._history[-MAX_HISTORY:]
            self._persist()
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(self, tuple(targets))
            except Exception:
                pass
        return restored

    def subscribe(self, listener: Callable[["SettingsStore", tuple[str, ...]], None]) -> None:
        self._listeners.append(listener)

    # --- UI snapshot --------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        """Grouped, UI-ready description of every setting (secrets masked)."""
        groups: dict[str, dict[str, Any]] = {}
        for spec in _SPECS:
            label, icon = _GROUP_LABELS.get(spec.group, (spec.group, "⚙️"))
            bucket = groups.setdefault(
                spec.group,
                {"id": spec.group, "label": label, "icon": icon, "items": []},
            )
            raw = self._secrets.get(spec.key) if spec.secret else self._values.get(spec.key, spec.default)
            item: dict[str, Any] = {
                "key": spec.key,
                "label": spec.label_ar,
                "help": spec.help_ar,
                "type": spec.type,
                "control": spec.ui_control,
                "value": mask_secret(raw) if spec.secret else raw,
                "is_default": (raw in (None, "", {}) if not spec.secret else not raw),
                "default": spec.default,
                "restart_required": spec.restart_required,
                "tags": list(spec.tags),
            }
            if spec.choices:
                item["choices"] = list(spec.choices)
            if spec.min_value is not None:
                item["min"] = spec.min_value
            if spec.max_value is not None:
                item["max"] = spec.max_value
            if spec.secret:
                item["secret"] = True
                item["configured"] = bool(raw)
            bucket["items"].append(item)
        return {
            "version": self._version,
            "groups": list(groups.values()),
            "history": list(reversed(self._history[-10:])),
            "editable": True,
        }

    def export(self) -> dict[str, Any]:
        """Portable, secret-free settings dump (for bug reports / the harness)."""
        return {key: value for key, value in self._values.items() if not SPEC_BY_KEY[key].secret}

    def describe(self) -> dict[str, Any]:
        """Flat, masked view used by telemetry."""
        return {
            spec.key: (mask_secret(self._secrets.get(spec.key)) if spec.secret else self._values.get(spec.key, spec.default))
            for spec in _SPECS
        }


_DEFAULT_STORE: SettingsStore | None = None
_DEFAULT_LOCK = threading.Lock()


def get_settings() -> SettingsStore:
    """Process-wide settings singleton."""
    global _DEFAULT_STORE
    with _DEFAULT_LOCK:
        if _DEFAULT_STORE is None:
            _DEFAULT_STORE = SettingsStore()
    return _DEFAULT_STORE


def set_settings_store(store: SettingsStore) -> None:
    """Test/diarness hook: install a pre-built store as the singleton."""
    global _DEFAULT_STORE
    with _DEFAULT_LOCK:
        _DEFAULT_STORE = store


__all__ = [
    "SettingSpec",
    "SettingsStore",
    "SettingsValidationError",
    "all_specs",
    "get_settings",
    "group_labels",
    "mask_secret",
    "set_settings_store",
    "SPEC_BY_KEY",
    "replace",
]
