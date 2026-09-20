"""Dataset layer for the Mizan evaluation harness.

Owns everything about the *test set*: loading, schema validation, selection
(filters by id/category/user/tag/outcome), and per-case execution plans
(repeats). Keeping this separate from the runner is what lets `--filter` and
`--only-failed` work without touching execution logic.

The dataset is a versioned artifact: ``test_set_version`` is recorded in every
report so a score is never comparable to a different dataset by accident.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SRC_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES_PATH = SRC_ROOT / "tests" / "test_cases.json"

CATEGORY_LABELS: dict[str, tuple[str, str]] = {
    "read_happy": ("استعلام عملاء", "🔍"),
    "read_product": ("استعلام مخزون", "📦"),
    "write_happy": ("إنشاء أمر بيع", "🧾"),
    "authz_denied": ("رفض الصلاحيات", "⛔"),
    "no_access": ("دور بلا صلاحيات", "🚫"),
    "ambiguous_entity": ("أسماء مبهمة", "🤔"),
    "duplicate_idempotency": ("منع التكرار", "♻️"),
    "erp_error": ("أخطاء ERP", "💥"),
    "prompt_injection": ("حقن تعليمات", "🛡️"),
}

_VALID_OUTCOMES = {
    "success",
    "not_found",
    "permission_denied",
    "disambiguation_request",
    "replay",
    "idempotency_conflict",
    "erp_validation_error",
    "confirmation_required",
}


class DatasetError(ValueError):
    """The dataset file is missing, malformed, or self-inconsistent."""


@dataclass(frozen=True)
class Attempt:
    """One execution of a case. A case may need several (replay semantics)."""

    index: int
    total: int
    auto_confirm: bool = True
    expect_outcome: str = "success"
    note: str = ""

    @property
    def label(self) -> str:
        return f"{self.index}/{self.total}"


@dataclass
class Case:
    """One evaluated Arabic utterance and everything expected of it."""

    id: str
    category: str
    user: str
    input: str
    expected_tool: str | None = None
    expected_args: Mapping[str, Any] | None = None
    expected_outcome: str = "success"
    auto_confirm: bool = False
    repeat: str | None = None
    notes: str = ""
    tags: tuple[str, ...] = ()
    line: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        name, icon = CATEGORY_LABELS.get(self.category, (self.category, "•"))
        return f"{icon} {name}"

    @property
    def attempts(self) -> list[Attempt]:
        """Execution plan.

        ``repeat: same_request`` (the idempotency cases) means: execute this
        exact request twice in the same environment — the first must be
        accepted, the second must be a replay. Expressing it here (instead of
        relying on global case ordering) is what makes the case hermetic and
        shardable.
        """
        if self.repeat == "same_request" or self.expected_outcome == "replay":
            return [
                Attempt(1, 2, auto_confirm=self.auto_confirm, expect_outcome="success", note="الأول: تنفيذ طبيعي"),
                Attempt(2, 2, auto_confirm=self.auto_confirm, expect_outcome="replay", note="الثاني: لازم replay"),
            ]
        return [Attempt(1, 1, auto_confirm=self.auto_confirm, expect_outcome=self.expected_outcome)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "user": self.user,
            "input": self.input,
            "expected_tool": self.expected_tool,
            "expected_args": dict(self.expected_args) if self.expected_args is not None else None,
            "expected_outcome": self.expected_outcome,
            "auto_confirm": self.auto_confirm,
            "repeat": self.repeat,
            "notes": self.notes,
            "tags": list(self.tags),
        }


@dataclass
class Dataset:
    version: str
    notes: str
    cases: list[Case]
    path: Path | None = None

    def __len__(self) -> int:
        return len(self.cases)

    @property
    def categories(self) -> list[str]:
        seen: list[str] = []
        for case in self.cases:
            if case.category not in seen:
                seen.append(case.category)
        return seen

    def by_id(self) -> dict[str, Case]:
        return {case.id: case for case in self.cases}


def load_dataset(path: Path | str = DEFAULT_CASES_PATH) -> Dataset:
    file_path = Path(path)
    if not file_path.exists():
        raise DatasetError(f"ملف حالات التقييم مش موجود: {file_path}")
    try:
        document = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise DatasetError(f"JSON غير صالح في {file_path.name}: {error}") from error
    raw_cases = document.get("test_cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise DatasetError("لا يوجد 'test_cases' غير فارغ في ملف الحالات")
    cases: list[Case] = []
    problems: list[str] = []
    seen_ids: set[str] = set()
    for position, row in enumerate(raw_cases, start=1):
        if not isinstance(row, Mapping):
            problems.append(f"الحالة رقم {position}: ليست كائن JSON")
            continue
        case_id = str(row.get("id") or "").strip()
        if not case_id:
            problems.append(f"الحالة رقم {position}: بدون id")
            continue
        if case_id in seen_ids:
            problems.append(f"{case_id}: معرّف مكرر")
        seen_ids.add(case_id)
        outcome = str(row.get("expected_outcome") or "success")
        if outcome not in _VALID_OUTCOMES:
            problems.append(f"{case_id}: expected_outcome غير معروف ({outcome})")
        if not str(row.get("input") or "").strip():
            problems.append(f"{case_id}: حقل input فاضي")
        tags = tuple(row.get("tags") or ()) if isinstance(row.get("tags"), (list, tuple)) else ()
        cases.append(
            Case(
                id=case_id,
                category=str(row.get("category") or "uncategorized"),
                user=str(row.get("user") or "sales_user@test"),
                input=str(row.get("input") or ""),
                expected_tool=row.get("expected_tool"),
                expected_args=row.get("expected_args") if isinstance(row.get("expected_args"), Mapping) else None,
                expected_outcome=outcome,
                auto_confirm=bool(row.get("auto_confirm")),
                repeat=row.get("repeat"),
                notes=str(row.get("notes") or ""),
                tags=tags,
                line=position,
                raw=dict(row),
            )
        )
    if problems:
        raise DatasetError("مشاكل في ملف الحالات:\n  - " + "\n  - ".join(problems[:20]))
    return Dataset(version=str(document.get("test_set_version") or "unversioned"), notes=str(document.get("notes") or ""), cases=cases, path=file_path)


def select(
    dataset: Dataset,
    *,
    ids: Sequence[str] = (),
    categories: Sequence[str] = (),
    users: Sequence[str] = (),
    outcomes: Sequence[str] = (),
    tags: Sequence[str] = (),
    pattern: str = "",
    search: str = "",
    limit: int | None = None,
) -> list[Case]:
    """Filter cases by any combination of selectors (AND across kinds, OR within)."""

    def matches(case: Case) -> bool:
        if ids and case.id not in set(ids):
            return False
        if categories and case.category not in set(categories):
            return False
        if users and case.user not in set(users):
            return False
        if outcomes and case.expected_outcome not in set(outcomes):
            return False
        if tags and not (set(tags) & set(case.tags)):
            return False
        if pattern:
            try:
                if not re.search(pattern, case.input, re.IGNORECASE):
                    return False
            except re.error:
                return pattern.lower() in case.input.lower()
        if search and search.lower() not in (case.input + " " + case.notes + " " + case.category).lower():
            return False
        return True

    chosen = [case for case in dataset.cases if matches(case)]
    if limit is not None:
        chosen = chosen[: max(0, int(limit))]
    return chosen


def parse_selector(raw: str | None) -> tuple[str, ...]:
    """``"TC-001 TC-004"`` / ``"TC-001,TC-004"`` → tuple of tokens."""
    if not raw:
        return ()
    return tuple(token for token in re.split(r"[\s,]+", str(raw).strip()) if token)


def select_tokens(dataset: Dataset, tokens: Sequence[str], *, limit: int | None = None) -> list[Case]:
    """Human-friendly selector parser for the CLI — faceted, like a test runner.

    Accepted forms: bare case id (or ``id:``), bare category (or ``category:``),
    bare user (or ``user:``), ``outcome:``, ``tag:``/``#tag``, ``text:`` and
    ``limit:N``. Tokens of the *same* kind are OR-ed, different kinds are AND-ed:
    ``--filter write_happy --filter read_happy`` runs both categories, and
    ``--category authz_denied --user readonly_user@test`` slices one inside the
    other. An unknown token is an error, and so is a selection that matches
    nothing — a silently empty run is the worst outcome of a typo'd filter,
    because it looks like a green build.
    """
    ids: list[str] = []
    categories: list[str] = []
    users: list[str] = []
    outcomes: list[str] = []
    tags: list[str] = []
    texts: list[str] = []
    effective_limit = limit
    known_categories = set(dataset.categories)
    known_users = {case.user for case in dataset.cases}
    for raw in tokens:
        token = str(raw).strip()
        if not token:
            continue
        kind, _, value = token.partition(":")
        if value:
            kind = kind.strip().lower().rstrip("s")
            value = value.strip()
            if kind == "limit":
                effective_limit = int(value)
                continue
            mapping = {"id": ids, "case": ids, "category": categories, "cat": categories, "user": users, "who": users, "outcome": outcomes, "tag": tags, "text": texts, "search": texts}
            if kind in mapping:
                mapping[kind].append(value)
                continue
        if token.upper().startswith("TC-") or token in dataset.by_id():
            ids.append(token)
            continue
        if token in known_categories or any(token in category for category in known_categories):
            matched = [category for category in known_categories if token in category]
            categories.extend(matched)
            continue
        if token in known_users or "@" in token:
            users.append(token)
            continue
        if token in _VALID_OUTCOMES:
            outcomes.append(token)
            continue
        if token.startswith("#"):
            tags.append(token[1:])
            continue
        prefix = token.lower()
        close = sorted(name for name in (known_categories | known_users | {case.id for case in dataset.cases}) if name.lower().startswith(prefix[:3]))
        raise DatasetError(
            f"محدّد غير معروف: {token!r}" + (f" — أقرب الخيارات: {', '.join(close[:6])}" if close else " — استخدم TC-xxx أو اسم الفئة أو المستخدم")
        )
    if not any((ids, categories, users, outcomes, tags, texts)):
        return list(dataset.cases)[: effective_limit if effective_limit else None]
    chosen = select(
        dataset,
        ids=ids,
        categories=categories,
        users=users,
        outcomes=outcomes,
        tags=tags,
        pattern=texts[0] if texts else "",
        search=" ".join(texts[1:]),
    )
    if not chosen:
        raise DatasetError(f"مفيش حالة مطابقة للمحدّدات: {', '.join(tokens)}")
    return chosen[: effective_limit] if effective_limit else chosen


def group_for_sharding(cases: Iterable[Case], strategy: str = "category") -> list[list[Case]]:
    """Partition cases into sequentially-executed groups (groups run in parallel).

    ``none`` keeps the historical single-shared-environment order (required for
    cases that intentionally depend on state written by earlier cases).
    ``category`` gives each category its own environment — safe because every
    category's expectations are hermetic once ``repeat`` is honoured internally.
    """
    buckets: dict[str, list[Case]] = {}
    for case in cases:
        key = case.category if strategy == "category" else "all"
        buckets.setdefault(key, []).append(case)
    return list(buckets.values())


def dataset_summary(dataset: Dataset) -> dict[str, Any]:
    counts: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    users: dict[str, int] = {}
    for case in dataset.cases:
        counts[case.category] = counts.get(case.category, 0) + 1
        outcomes[case.expected_outcome] = outcomes.get(case.expected_outcome, 0) + 1
        users[case.user] = users.get(case.user, 0) + 1
    return {
        "version": dataset.version,
        "cases": len(dataset.cases),
        "by_category": counts,
        "by_outcome": outcomes,
        "by_user": users,
        "executions_planned": sum(len(case.attempts) * max(1, 1) for case in dataset.cases),
    }


__all__ = [
    "Attempt",
    "CATEGORY_LABELS",
    "Case",
    "DEFAULT_CASES_PATH",
    "Dataset",
    "DatasetError",
    "dataset_summary",
    "group_for_sharding",
    "load_dataset",
    "parse_selector",
    "select",
    "select_tokens",
]
