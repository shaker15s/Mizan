"""The ``DecisionClient`` protocol — the only seam the runtime knows about.

A decision client takes a *state* plus a *question set* and returns normalized
:class:`~poc.decision.models.DecisionResult` objects. Every provider (mock,
TypeSafe/Jev, a future ERPNext-specific decision model, a customer's own
endpoint) implements exactly this, so replacing Jev never touches
``AgentRuntime`` or ``ToolGateway`` (plan §9).

Deliberate properties:

- **Synchronous + narrow.** One method. No callbacks, no streaming, no
  provider objects escaping.
- **No authority in the signature.** Nothing about users, tenants, tools,
  policy, or the gateway is passed to a decision client except text that has
  already been redacted. A provider physically cannot select a tenant, mint an
  idempotency key, or execute a tool (plan §10, §23).
- **Failures are values.** Implementations return a ``DecisionResult`` with
  ``error`` set; they do not raise provider exceptions into the runtime.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable

from poc.decision.models import DecisionResult


@runtime_checkable
class DecisionClient(Protocol):
    """Minimal contract every decision provider must satisfy."""

    #: Human-readable provider name recorded in evidence/telemetry (``mock``, ``typesafe``...).
    provider: str

    #: Provider default model id (``jev-latest``, ``mock-deterministic``...).
    model: str

    def decide(
        self,
        state: Any,
        questions: Mapping[str, Mapping[str, Any]],
        *,
        spec_version: str,
        state_hash: str | None = None,
        timeout_s: float | None = None,
    ) -> DecisionResult:
        """Evaluate ``questions`` against ``state`` and normalize the answers."""
        ...

    def health(self) -> Mapping[str, Any]:
        """Provider-neutral health/telemetry snapshot (never contains secrets)."""
        ...

    def close(self) -> None:
        """Release transport resources; must be safe to call twice."""
        ...


__all__ = ["DecisionClient"]
