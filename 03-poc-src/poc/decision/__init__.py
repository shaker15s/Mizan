"""MIZAN Decision Intelligence layer (Jev integration).

The decision layer is a **provider-neutral, typed, additive** signal source.
It never authorizes, executes, approves, or verifies anything.

Authority model (plan §2, §10, §42):

    LLM      → understand / generate / propose      (untrusted)
    Decision → fast typed signal                     (untrusted)
    MIZAN    → govern / authorize / execute / verify (server-owned)
    ERP      → authoritative business truth

Everything the decision layer produces is *evidence about a decision*, never a
decision. The :class:`~poc.decision.policy.DecisionPolicy` converts signals into
**monotonic** adjustments: a signal may only ever increase conservatism
(escalate, narrow candidates, request clarification, quarantine) and can never
remove a deterministic control.

Public surface (see each module for details):

* :mod:`poc.decision.models`     — typed answers, results, error taxonomy
* :mod:`poc.decision.protocol`   — the ``DecisionClient`` protocol
* :mod:`poc.decision.questions`  — versioned question spec + state builders
* :mod:`poc.decision.redaction`  — default-deny state redactor
* :mod:`poc.decision.thresholds` — versioned, calibrated thresholds
* :mod:`poc.decision.mock_client`— deterministic offline provider
* :mod:`poc.decision.jev_client` — provider-neutral HTTP adapter (official/custom)
* :mod:`poc.decision.policy`     — monotonic combination rules
* :mod:`poc.decision.router`     — mode orchestration + evidence/audit wiring
* :mod:`poc.decision.telemetry`  — counters, latency, health
* :mod:`poc.decision.cache`      — conservative, opt-in decision cache
"""

from __future__ import annotations

DECISION_LAYER_VERSION = "1.0.0"

__all__ = ["DECISION_LAYER_VERSION"]
