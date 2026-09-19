"""JSON renderer — the machine-readable contract.

The JSON document is the source of truth that ``--baseline`` diffs and CI jobs
consume, so it must stay stable in shape and grow additively.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def render(report: Mapping[str, Any], *, indent: int = 2) -> str:
    return json.dumps(report, ensure_ascii=False, indent=indent, default=str, sort_keys=False)


def write(report: Mapping[str, Any], path: Path | str, *, indent: int = 2) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(report, indent=indent) + "\n", encoding="utf-8")
    return target


__all__ = ["render", "write"]
