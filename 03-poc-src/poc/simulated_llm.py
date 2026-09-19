"""A deterministic, dependency-free Arabic intent engine (demo / offline mode).

``SimulatedLLMClient`` implements the exact same
:class:`~poc.llm_client.LLMClientProtocol` as the Anthropic and
OpenAI-compatible adapters, so it drives the **identical** pipeline:
intent → tool call → schema validation → authz → confirmation → ERP →
verification → audit. Nothing is faked downstream; only the language model is
replaced by a rule engine.

Why it exists:
- demos without an API key or network (a buyer's laptop, a CI preview),
- a stable lower bound for the eval harness when no provider is reachable,
- instant answers for the cockpit's offline mode.

It is *deliberately* excluded from the scored live evaluation: a rule engine
agreeing with the expected tool would be circular. ``/api/telemetry`` and every
answer card expose ``engine: simulated`` so nobody mistakes it for the model.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from poc.llm_client import (
    LLMMessage,
    LLMResponse,
    LLMToolCall,
    LLMToolDefinition,
)
from poc.normalization import normalize_arabic

_WRITE_HINTS = (
    "اعمل", "اعملو", "اظافه", "اضافة", "انشاء", "انشئ", "افتح", "سجل", "اطلق", "ابعت طلب",
    "اوردر جديد", "طلب بيع", "اطلب", "نفذ", "خللص", "اكمل الطلب", "command create", "create order", "new order",
)
_ORDER_HINTS = ("اوردر", "أمر", "طلب", "order", "فاتوره", "شحنه")
_PRODUCT_HINTS = ("منتج", "اصناف", "صنف", "مخزون", "سعر", "اسعار", "متاح", "كميه", "product", "stock", "price")
_CUSTOMER_HINTS = ("عميل", "زبون", "شركه", "مؤسسه", "متجر", "customer", "client", "partner")
_DETAIL_HINTS = ("تفاصيل", "بطاقه", "بيانات", "حاله", "فين", "اين", "رصيد", "حد ائتماني")
_CHAT_HINTS = ("السلام عليكم", "اهلا", "أهلا", "صباح", "مساء", "شكرا", "امتنان", "مين انت", "ايش تقدر", "help", "مساعده")

_QUANTITY_RE = re.compile(r"(?:عدد|كميه|كمية|qty|لعدد|بكميه|قطعه|قطعة|كرتون|وحده)\s*[:=]?\s*(\d+(?:[.,]\d+)?)|\s*(\d+(?:[.,]\d+)?)\s*(?:قطعه|قطعة|كرتون|وحده|كيلو|حبه|حبة)", re.I)
_ID_SEP = r"(?:\s*(?:الرقم|رقم|id|code|كود|#|:))?\s*"
ID_PREFIX = r"(?:الرقم|رقم|id|كود)\s*(?:بتاع|تاع|حق|ديال|)?\s*"
_PRODUCT_ID_RE = re.compile(rf"(?:المنتج|منتج|product){_ID_SEP}(\d{{1,6}})", re.I)
_CUSTOMER_ID_RE = re.compile(rf"(?:(?:العميل|عميل|زبون|customer|partner){_ID_SEP}|{ID_PREFIX}(?:العميل|عميل|زبون)\s*)(\d{{1,9}})", re.I)
_ORDER_ID_RE = re.compile(rf"(?:الاوردر|اوردر|الامر|الأمر|أمر|order|الطلب|طلب){_ID_SEP}(\d{{1,9}})", re.I)
_TRAILING_NUMBERS_RE = re.compile(r"(?<![\d.,])\d{1,9}(?![\d.])")
# Order lines come only from explicit quantity↔id shapes, so a customer id can
# never be read as a quantity: «20 من المنتج 55», «2 مياه (55)», «منتج 56، الكمية 5».
_UNIT = "قطعه|قطعة|قطع|كرتون|كرتونه|وحد|وحده|وحدات|كيلو"
_PRODUCT_WORD = "المنتج|منتج|product|صنف|الاصناف|الأصناف"
_QTY_WORD = "الكميه|الكمية|كميه|كمية|qty|عدد"
_QTY_CUE = "لعدد|عدد|بكميه|بكمية|كميه|كمية|qty|قطعه|قطعة|كيلو|وحد|حبه|حبة|بـ"
# Two shapes only — a quantity cue in front of the number («لعدد 20 … المنتج 55») or
# «من» between them («2 من المنتج 57»). Anything looser reads the customer id as a
# quantity, which is how a rule engine ends up ordering 43 crates of water.
_LINE_FWD_CUE_RE = re.compile(rf"(?:{_QTY_CUE})\s*(\d+(?:[.,]\d+)?)\s*(?:{_UNIT}\s*)?(?:من\s+)?(?:{_PRODUCT_WORD})\s*#?\s*(\d{{1,6}})", re.I)
_LINE_FWD_MIN_RE = re.compile(rf"(\d+(?:[.,]\d+)?)\s*(?:{_UNIT}\s*)?من\s+(?:{_PRODUCT_WORD})\s*#?\s*(\d{{1,6}})", re.I)
_LINE_FWD_RE = _LINE_FWD_CUE_RE
_LINE_PAREN_RE = re.compile(rf"(\d+(?:[.,]\d+)?)\s*[^\d()]{{1,30}}?\(\s*(?:{_PRODUCT_WORD})?\s*#?\s*(\d{{1,6}})\)", re.I)
_LINE_REV_RE = re.compile(rf"(?:{_PRODUCT_WORD})\s*#?\s*(\d{{1,6}})[^\d]{{0,16}}?(?:{_QTY_WORD})\s*:?\s*(\d+(?:[.,]\d+)?)", re.I)


_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def _norm(text: str) -> str:
    """Matching form: Arabic orthography folded, Eastern-Arabic digits converted.

    «لعدد ٢٠» must parse as quantity 20 — a rule engine that cannot read the
    digits the user actually typed is not a demo engine at all.
    """
    return normalize_arabic(str(text or "").translate(_INDIC_DIGITS)).replace("؟", " ").replace("?", " ").replace("،", " ")


_NAME_MARK_RE = re.compile(r"(?:اسمه|اسمها|المسمى|اللي اسم)")


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    """First non-empty capture group (patterns may offer alternative shapes)."""
    for match in pattern.finditer(text):
        for group in match.groups():
            if group:
                return group
    return None


def _has(text: str, needles: Iterable[str]) -> bool:
    return any(needle in text for needle in needles)


def _extract_numbers(text: str) -> list[int]:
    out: list[int] = []
    for raw in _TRAILING_NUMBERS_RE.findall(text):
        try:
            out.append(int(raw))
        except ValueError:  # pragma: no cover - regex guarantees digits
            continue
    return out


# ---------------------------------------------------------------- name extraction
# Egyptian requests wrap the entity in politeness and scaffolding («هاتلي بيانات
# العميل اللي اسمه محمد عندنا؟»). A model extracts «محمد»; the offline engine has
# to do the same or the demo searches whole sentences and finds nothing.
#
# The extractor works on a *length-preserving* fold (hamza/ة/case only) so every
# match index maps straight back onto the original characters — the returned name
# keeps the user's own spelling (أحمد, IGNORE ALL RULES), never the folded form.

_IMPERATIVES = (
    "هاتلي", "هات", "هاتي", "جيبلي", "جيب", "اجبلي", "اجيب", "اعرضلي", "اعرض", "ابعتلي", "ابعت",
    "وريني", "ورني", "اعطني", "اعطنى", "قوليلي", "قولي", "عرفني", "دورلي", "دور", "ابحث", "شوف",
    "عايز", "محتاج", "اريد", "نفذ", "سجل", "جهز", "زود", "كمل", "دلني", "فين", "اين",
    "اعمل", "اعملو", "عمل", "اظافه", "اضافه", "انشئ", "انشاء", "افتح", "اطلق", "ابعت", "هاتنفذ",
)
# Particles: stripped from the head only when a name follows (so «علي» as a whole
# answer is never eaten).
_PARTICLES = ("على", "عن", "في", "من", "اللي", "اللى", "الي", "بتاع", "عشان", "علشان", "لي", "للمرة")
# Words that are never part of the entity itself.
_FILLERS = (
    "لي", "ليا", "يا", "فضلا", "فضلك", "سمحت", "بسرعه", "بليز", "طيب", "تمام", "حبيبي", "بس",
    "عندنا", "عندك", "عندكم", "متاح", "متوفر", "متوفرين", "اليوم", "النهارده", "دلوقتي", "هل",
    "ايه", "كل", "فقط", "بكام", "السعر", "اسعار", "سعر", "كم", "تقريبا", "لو", "فيه", "فيها",
    "اكتر", "قولي", "بالظبط", "كمان", "تاني", "جديد", "طلب", "بيع", "حالي", "الحالي", "ممكن",
)
# Entity/category words: safe to drop when the name is still left behind.
_SCAFFOLD_WORDS = (
    "العميل", "عميل", "العملاء", "عملاء", "الزبون", "زبون", "المورد", "مورد",
    "المنتج", "منتج", "المنتجات", "منتجات", "الصنف", "صنف", "الاصناف", "اوردر", "الاوردر", "أمر", "الامر",
    "الطلب", "الطلبات", "بطاقه", "بطاقة", "بيانات", "تفاصيل", "حاله", "حالة", "رقم", "كود", "تليفون",
    "التليفون", "موبايل", "الموبايل", "هاتف", "الهاتف", "الفون", "اعرف", "اشوف", "ملاحظاته", "اسماء",
    "اسامى", "اساميه", "اسم", "الاسم", "عنوان", "العنوان", "بريد", "البريد", "ايميل", "ميل",
)
# Multi-word phrases that only ever introduce the name.
_SCAFFOLD_PHRASES = (
    "العملاء اللي في اساميهم", "العملاء اللي اسمائهم", "العملاء اللي اسماءهم", "العملاء اللي عندهم",
    "المنتجات اللي اسمها فيها", "المنتجات اللي فيها", "الاصناف اللي فيها", "العميل اللي اسمه",
    "اللي مكتوب في ملاحظاته", "مكتوب في ملاحظاته", "في ملاحظاته", "اللي اسمه", "اللي اسمها", "المسمى بـ",
)
# Everything from one of these onwards is a qualifier, not the name.
_STOP_PHRASES = (
    " - ", "—", "عندنا", "عندك", "عندكم", "متاح", "متوفر", "بكام", "السعر", "اسعار", "لو ", "سعر",
    "بكميه", "بكمية", "كميه", "كمية", "لعدد", "عدد", "قطعه", "قطع", "وحده", "من ", "كرتون", "حاله",
    "حالة", "لproduct", "product", "،", ",", "؟", "?",
)
# Single definite words that are catalogue nouns, not proper names: «الزيت» → «زيت».
_GENERIC_NOUNS = {"زيت", "سكر", "ارز", "شاي", "مياه", "لبن", "عسل", "تمر", "ملح", "دقيق", "صابون", "فول", "عدس"}
# «لـ» as a dative prefix on the name itself (write phrasing: «اعمل طلب لمحمد»).
_DATIVE_PREFIXES = ("للموظف", "للعميل", "لعميل", "للزبون", "لزبون", "لمورد", "للمورد", "للشركه", "ل",)

_FOLD = str.maketrans('أإآٱؤئءةى', 'ااااوياهي')


def _fold(text: str) -> str:
    """Length-preserving fold used for matching and index math."""
    folded = str(text or "").translate(_FOLD).lower()
    return folded if len(folded) == len(text) else str(text or "").lower()


_DIACRITICS_RE = re.compile(r"[\u064B-\u0652\u0640\u0670]")


def _key(text: str) -> str:
    """Comparison key: folded *and* vowel/tatweel-free (sets only, never indexes)."""
    return _DIACRITICS_RE.sub("", _fold(text))


def _name_patterns(folded: str) -> tuple[int, int] | None:
    """Spans of an explicitly named entity: «اللي اسمه X», «في ملاحظاته X»."""
    # Longest alternative first: «اسمها» must not be matched as «اسمه» + stray ا.
    direct = re.compile(r"(?:اللي\s+)?(?:اسمها|اسمه|المسمى|المسمي|ملاحظاته)\s*[:=]?\s*(.+)$")
    match = direct.search(folded)
    if not match:
        return None
    return match.span(1)


def _clean_span(folded: str, start: int, end: int) -> str:
    """Clip a span at the first qualifier phrase and drop trailing punctuation."""
    window = folded[start:end]
    cut = len(window)
    for stop in _STOP_PHRASES:
        probe = _fold(stop)
        index = window.find(probe)
        if index >= 0:
            cut = min(cut, index)
    return window[:cut].strip(" \t،,.؛;:-")


def extract_query_name(text: str) -> str:
    """Pull the named entity out of an Arabic request (``query`` argument).

    Priority: an explicit naming phrase («اللي اسمه …», «… في ملاحظاته …») →
    scaffold-and-verb stripping of the whole sentence. Returns ``""`` when no
    name-shaped fragment survives, so the caller can refuse to guess.
    """
    original = " ".join((text or "").split())
    if not original:
        return ""
    folded = _fold(original)
    # ``original`` may contain runs of spaces collapsed above; rebuild both with
    # single spaces so indices stay aligned.
    original = re.sub(r"\s+", " ", original)
    folded = re.sub(r"\s+", " ", folded)

    span = _name_patterns(folded)
    if span is not None:
        window_start, window_end = span
        trimmed = _clean_span(folded, window_start, window_end)
        candidate = original[window_start : window_start + len(trimmed)]
        tokens = _strip_edges(candidate.split())
        result = " ".join(tokens).strip(" ،.؟?")
        if result and not result.isdigit():
            return result[:60]

    working = list(original)
    masked = [False] * len(working)

    def blank(phrase: str) -> None:
        probe = _fold(phrase)
        for match in re.finditer(re.escape(probe), folded):
            for index in range(match.start(), match.end()):
                masked[index] = True

    for phrase in sorted(_SCAFFOLD_PHRASES, key=len, reverse=True):
        blank(phrase)
    # Cut the tail at the first qualifier — but only if a name-shaped fragment
    # survives before it («كم سعر الزيت؟» must keep «الزيت», not be cut at «سعر»).
    candidates = sorted(
        {index for stop in _STOP_PHRASES for index in [folded.find(_fold(stop))] if index > 0}
    )
    head_cut = len(folded)
    for cut in candidates:
        preview = "".join(char if not flag else " " for index, (char, flag) in enumerate(zip(working, masked)) if index < cut)
        if _strip_edges(preview.split()):
            head_cut = cut
            break
    for index in range(head_cut, len(folded)):
        masked[index] = True

    kept = [
        token
        for token in "".join(char if not flag else " " for char, flag in zip(working, masked)).split()
    ]
    kept = _strip_edges(kept)
    result = " ".join(kept).strip(" ،.؟?-")
    if _key(result) in {_key(word) for word in _SCAFFOLD_WORDS} | {_key(word) for word in _FILLERS}:
        return ""
    if re.search(r"(.)\1{3,}", _key(result.replace(" ", ""))):
        return ""  # «اااااه» is a sigh, not a search term
    if result and not result.isdigit():
        if len(result.split()) == 1 and _fold(result).startswith("ال") and _fold(result)[2:] in _GENERIC_NOUNS:
            result = result[2:]
        return result[:60]
    return ""


def _strip_edges(tokens: list[str]) -> list[str]:
    """Trim polite verbs, particles, and category nouns from both ends."""
    imperatives = {_key(word) for word in _IMPERATIVES}
    fillers = {_key(word) for word in _FILLERS}
    scaffolds = {_key(word) for word in _SCAFFOLD_WORDS}
    particles = {_key(word) for word in _PARTICLES}
    kept = [token for token in tokens if re.search(r"[\u0600-\u06FFA-Za-z]", token)]
    changed = True
    while kept and changed:
        changed = False
        probe = _key(kept[0]).strip("،.:")
        if probe in imperatives or probe in fillers or (len(kept) > 1 and (probe in scaffolds or probe in particles)):
            kept.pop(0)
            changed = True
            continue
        stripped = _strip_dative(kept[0])
        if stripped:
            kept[0] = stripped
            changed = True
            continue
    changed = True
    while len(kept) > 1 and changed:
        changed = False
        probe = _key(kept[-1]).strip("،.:")
        if probe in fillers or probe in scaffolds:
            kept.pop()
            changed = True
    return [token for token in kept if _key(token).strip("،.:?؟")]


def _strip_dative(token: str) -> str:
    """«لمحمد» → «محمد» (the dative prefix carries no information for a search)."""
    folded = _fold(token)
    for prefix in sorted((_key(word) for word in _DATIVE_PREFIXES), key=len, reverse=True):
        if not prefix or prefix == "ل":
            continue
        if folded.startswith(prefix) and len(folded) - len(prefix) >= 2:
            return token[len(prefix) :]
    if folded.startswith("ل") and len(folded) >= 3 and not folded[1:3] == "ال":
        remainder = token[1:]
        probe = _key(remainder)
        if probe:
            return remainder
    return ""


class SimulatedLLMClient:
    """Rule-driven ``LLMClientProtocol`` implementation (no network, no key)."""

    provider = "simulated"

    def __init__(self, *, known_names: Mapping[str, Iterable[str]] | None = None, confidence_note: bool = True) -> None:
        # ``known_names`` optionally biases name splitting with a vocabulary
        # (e.g. ``{"customer": ["محمد أحمد", ...]}``) — used by the cockpit demo.
        self._vocabulary = {key: [normalize_arabic(name) for name in values] for key, values in (known_names or {}).items()}
        self.confidence_note = confidence_note
        self.calls: list[dict[str, Any]] = []

    # --- protocol -----------------------------------------------------------
    def chat(
        self,
        messages: list[LLMMessage],
        system: str | None = None,
        tools: list[LLMToolDefinition] | None = None,
    ) -> LLMResponse:
        self.calls.append({"messages": list(messages), "system": system, "tools": tools})
        user_text = next((m.content for m in reversed(messages) if m.role == "user"), "")
        available = {tool.name for tool in (tools or [])}
        return self._respond(user_text, available, messages)

    # --- intent engine ------------------------------------------------------
    def _respond(self, raw_text: str, available: set[str], messages: list[LLMMessage]) -> LLMResponse:
        text = _norm(raw_text)
        if not text.strip():
            return self._text("اكتب طلبك بصراحة: اسم عميل للاستعلام، أو «اعمل أمر بيع للعميل 42 …» للتنفيذ.")
        if _has(text, _CHAT_HINTS) and not re.search(r"\d", text):
            return self._text(
                "أنا ميزان — بوابة الـ ERP. أقدر أدوّر على عميل أو منتج، أجيب تفاصيل أمر بيع، "
                "أو أجهّز أمر بيع جديد بتوقيعك. جرّب: «ابحث عن محمد أحمد» أو «اعمل أمر بيع للعميل 42 لعدد 15 من المنتج 55»."
            )
        history_ids = self._history_ids(messages)
        customer_id = _first(_CUSTOMER_ID_RE, text)
        order_id = _first(_ORDER_ID_RE, text)
        quantity = _first(_QUANTITY_RE, text)
        # «اعمل أوردر للعميل 42» has no imperative from the write list, but an order
        # plus a customer id or a quantity is unambiguously an execution request.
        create_intent = _has(text, _WRITE_HINTS) or (_has(text, _ORDER_HINTS) and (customer_id or quantity))
        named_customer = extract_query_name(raw_text)
        explicit_name = bool(_NAME_MARK_RE.search(text))

        if create_intent and "sales.order.create" in available:
            call = self._create_args(text, history_ids)
            if call is not None:
                return self._tool("sales.order.create", call[0], "إنشاء أمر", extra_note=call[1])
            if named_customer:
                # A write addressed by name, not id: never invent an id — resolve
                # the ambiguity first, which is exactly the governed behaviour.
                return self._tool("customer.search", {"query": named_customer}, "توضيح قبل التنفيذ")

        if order_id and "sales.order.get" in available:
            return self._tool("sales.order.get", {"order_id": int(order_id)}, "أمر بيع")
        if customer_id and "customer.get" in available and not _has(text, _PRODUCT_HINTS):
            return self._tool("customer.get", {"customer_id": int(customer_id)}, "عميل")

        if "product.search" in available and not create_intent and self._is_product_query(text, named_customer):
            return self._tool("product.search", {"query": named_customer or _norm(raw_text).strip()}, "مخزون")

        if "customer.search" in available and named_customer:
            return self._tool("customer.search", {"query": named_customer}, "عميل")
        if explicit_name and "customer.search" in available:
            return self._tool("customer.search", {"query": extract_query_name(raw_text) or text[:40]}, "عميل")

        return self._text(
            "مش قادر أحدد العملية من الجملة دي. قوللي: دوّر على عميل «اسم»، أو افحص منتج «اسم»، "
            "أو استخدم الصيغة دي: اعمل أمر بيع للعميل 42 لعدد 15 من المنتج 55."
        )

    @staticmethod
    def _is_product_query(text: str, name: str) -> bool:
        """Products, not customers, when the ask is about stock/price or a catalogue noun."""
        if _has(text, _CUSTOMER_HINTS) and not _has(text, ("منتج", "اصناف", "مخزون")):
            return False
        if _has(text, _PRODUCT_HINTS):
            return True
        probe = _norm(name)
        return any(token in _GENERIC_NOUNS for token in probe.split())

    # --- helpers ------------------------------------------------------------
    @staticmethod
    def _text(content: str) -> LLMResponse:
        return LLMResponse(text=content, tool_calls=(), raw={"provider": "simulated", "engine": "mizan-rules-v1"})

    @staticmethod
    def _tool(name: str, arguments: Mapping[str, Any], label: str = "", *, extra_note: str = "") -> LLMResponse:
        note = extra_note or f"محاكاة: نويت أن أستدعي {name} ({label}) بالمعاملات المرفقة."
        return LLMResponse(
            text=None,
            tool_calls=(LLMToolCall(name=name, arguments=dict(arguments), call_id=f"sim-{name}"),),
            raw={"provider": "simulated", "engine": "mizan-rules-v1", "note": note},
        )

    @staticmethod
    def _numbers(text: str) -> int | None:
        numbers = _extract_numbers(text)
        return numbers[0] if numbers else None

    @staticmethod
    def _first_group(pattern: re.Pattern[str], text: str) -> str | None:
        match = pattern.search(text)
        return match.group(1) if match else None

    @staticmethod
    def _history_ids(messages: list[LLMMessage]) -> dict[str, int]:
        """Recover ids mentioned in prior turns (supports «نفس العميل» follow-ups)."""
        found: dict[str, int] = {}
        for message in messages[:-1]:
            content = _norm(message.content)
            if "order" not in found:
                order = SimulatedLLMClient._first_group(_ORDER_ID_RE, content)
                if order:
                    found["order"] = int(order)
            if "customer" not in found:
                customer = SimulatedLLMClient._first_group(_CUSTOMER_ID_RE, content)
                if customer:
                    found["customer"] = int(customer)
        return found

    def _query_for(self, normalized: str, original: str, kind: str) -> str:
        vocabulary = self._vocabulary.get(kind, [])
        probe = normalize_arabic(original)
        for name in vocabulary:
            if name and name in probe:
                return name
        return extract_query_name(original)

    def _create_args(self, normalized: str, history_ids: Mapping[str, int]) -> tuple[dict[str, Any], str] | None:
        """Build a create payload from explicit evidence only.

        Rule engines must not guess: a customer id is valid when the sentence says
        «العميل 42», a product id when it says «منتج 55» (or names it with an id in
        parentheses), a quantity when it says «لعدد 20»/«2 مياه». Anything else →
        ``None`` so the caller asks instead of creating a wrong order.
        """
        customer_id = _first(_CUSTOMER_ID_RE, normalized) or history_ids.get("customer")
        product_id = _first(_PRODUCT_ID_RE, normalized)
        quantity = _first(_QUANTITY_RE, normalized)
        lines = _parse_lines(normalized)
        if not lines and product_id:
            lines = [{"product_id": int(product_id), "quantity": _as_number(quantity, default=1.0)}]
        if customer_id is None or not lines:
            return None
        payload: dict[str, Any] = {"customer_id": int(customer_id), "lines": lines}
        summary = "، ".join(f"{line['quantity']} × صنف {line['product_id']}" for line in lines)
        note = f"محاكاة: إنشاء أمر بيع للعميل {customer_id} ({summary}) — يحتاج توقيعك."
        return (payload, note)


def _as_number(raw: Any, *, default: float) -> float:
    try:
        value = float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _parse_lines(text: str) -> list[dict[str, Any]]:
    """``2 مياه معدنية (55) و1 زيت (56)`` / ``6 من المنتج 57`` → order lines.

    Ids are mandatory; product names without a resolvable id are *not* guessed —
    the governed path is to search for the product first.
    """
    pairs: list[dict[str, Any]] = []
    taken: list[tuple[int, int]] = []
    for pattern, ordered in ((_LINE_FWD_CUE_RE, True), (_LINE_FWD_MIN_RE, True), (_LINE_PAREN_RE, True), (_LINE_REV_RE, False)):
        for match in pattern.finditer(text):
            first, second = match.groups()
            if not first or not second:
                continue
            # Overlapping shapes describe the *same* line («لعدد 20 من المنتج 55» is
            # both a cue-form and a من-form); counting it twice doubles the quantity.
            if any(match.start() < stop and match.end() > start for start, stop in taken):
                continue
            qty_raw, prod_raw = (first, second) if ordered else (second, first)
            try:
                quantity, product_id = float(qty_raw), int(prod_raw)
            except (TypeError, ValueError):
                continue
            if quantity <= 0:
                continue
            taken.append((match.start(), match.end()))
            pairs.append({"product_id": product_id, "quantity": quantity})
    if pairs:
        deduped: dict[int, float] = {}
        for row in pairs:
            deduped[row["product_id"]] = deduped.get(row["product_id"], 0.0) + float(row["quantity"])
        return [
            {"product_id": product_id, "quantity": int(qty) if qty == int(qty) else qty}
            for product_id, qty in sorted(deduped.items())
        ]
    quantity = _first(_QUANTITY_RE, text)
    product_id = _first(_PRODUCT_ID_RE, text)
    if quantity and product_id:
        value = float(quantity)
        return [{"product_id": int(product_id), "quantity": int(value) if value == int(value) else value}]
    return []


__all__ = ["SimulatedLLMClient", "extract_query_name"]
