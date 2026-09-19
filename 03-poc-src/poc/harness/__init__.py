"""Mizan evaluation harness.

A from-scratch rebuild of ``poc/tests/run_eval.py`` with one job: tell the truth
about whether Mizan is safe and useful, fast enough to run on every keystroke.

    python -m poc.harness                       # deterministic CI run + verdict
    python -m poc.harness --mode live           # score real model intelligence
    python -m poc.harness --filter write_happy  # one slice
    python -m poc.harness doctor                # trust the harness itself
    python -m poc.harness explain TC-044        # why did it fail?

Layers: ``cases`` (dataset/selection) → ``environment`` (seeded ERP + store) →
``runner`` (execute) → ``graders`` (deterministic checks) → ``metrics`` →
``report`` (gates + baseline diff) → ``render`` (console/json/md/html).
"""

from __future__ import annotations

__version__ = "2.0.0"
__all__ = ["__version__"]
