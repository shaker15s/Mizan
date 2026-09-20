"""Deterministic authorization policy engine for the POC.

Loads a server-owned YAML policy file and evaluates authenticated security
context against it. This layer performs no tool execution, no Odoo calls,
no LLM calls, and no audit persistence. Odoo remains the final external
authorization boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from poc.tool_contracts import ToolRegistry, get_registry

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[1] / "users.yaml"

ALLOWED = "allowed"
DENIED = "denied"
CONFIRMATION_REQUIRED = "confirmation_required"

RULE_MALFORMED_REQUEST = "MALFORMED_REQUEST"
RULE_UNKNOWN_USER = "UNKNOWN_USER"
RULE_UNKNOWN_TENANT = "UNKNOWN_TENANT"
RULE_TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
RULE_TOOL_VERSION_MISMATCH = "TOOL_VERSION_MISMATCH"
RULE_EXPLICIT_DENY = "EXPLICIT_DENY"
RULE_PERMISSION_DENIED = "PERMISSION_DENIED"
RULE_CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
RULE_ALLOWED = "ALLOWED"


class PolicyConfigurationError(ValueError):
    """Raised when the server-owned policy is malformed or unsafe."""


@dataclass(frozen=True)
class PolicyDecision:
    """Immutable authorization result for the gateway and audit layers."""

    decision: str
    user_id: str | None
    tenant_id: str | None
    tool_name: str | None
    tool_version: str | None
    reason: str
    policy_rule_id: str
    requires_confirmation: bool


@dataclass(frozen=True)
class PolicyUser:
    """Validated server-side policy identity."""

    user_id: str
    tenant_id: str
    role: str
    allowed_tools: tuple[str, ...]
    denied_tools: tuple[str, ...]


def _load_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return yaml.safe_load(stream)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise PolicyConfigurationError(f"malformed or unreadable policy: {path}") from error


def _validate_policy(policy: Any, registry: ToolRegistry) -> dict[str, PolicyUser]:
    if not isinstance(policy, dict):
        raise PolicyConfigurationError("policy must be a mapping")
    users = policy.get("users")
    if not isinstance(users, dict) or not users:
        raise PolicyConfigurationError("policy.users must be a non-empty mapping")
    if set(policy) != {"users"}:
        raise PolicyConfigurationError("policy has unexpected top-level fields")

    known_tools = set(registry.names())
    parsed: dict[str, PolicyUser] = {}
    for user_id, spec in users.items():
        if not isinstance(user_id, str) or not user_id or not isinstance(spec, dict):
            raise PolicyConfigurationError("each policy user must be a non-empty mapping")
        unexpected_user_fields = set(spec) - {"tenant_id", "role", "allowed_tools", "denied_tools"}
        if unexpected_user_fields:
            raise PolicyConfigurationError(
                f"policy user {user_id} has unexpected fields: {', '.join(sorted(unexpected_user_fields))}"
            )
        tenant_id = spec.get("tenant_id")
        role = spec.get("role")
        allowed_tools = spec.get("allowed_tools")
        denied_tools = spec.get("denied_tools", [])
        if not isinstance(tenant_id, str) or not tenant_id:
            raise PolicyConfigurationError(f"policy user {user_id} has invalid tenant_id")
        if not isinstance(role, str) or not role:
            raise PolicyConfigurationError(f"policy user {user_id} has invalid role")
        if not isinstance(allowed_tools, list):
            raise PolicyConfigurationError(f"policy user {user_id} has invalid allowed_tools")
        if not isinstance(denied_tools, list):
            raise PolicyConfigurationError(f"policy user {user_id} has invalid denied_tools")
        for tool_name in [*allowed_tools, *denied_tools]:
            if not isinstance(tool_name, str) or tool_name not in known_tools:
                raise PolicyConfigurationError(
                    f"policy user {user_id} references unknown tool {tool_name!r}"
                )
        overlap = set(allowed_tools) & set(denied_tools)
        if overlap:
            raise PolicyConfigurationError(
                f"policy user {user_id} has contradictory tools: {', '.join(sorted(overlap))}"
            )
        parsed[user_id] = PolicyUser(
            user_id=user_id,
            tenant_id=tenant_id,
            role=role,
            allowed_tools=tuple(allowed_tools),
            denied_tools=tuple(denied_tools),
        )
    return parsed


def _denied(
    user_id: Any,
    tenant_id: Any,
    tool_name: Any,
    tool_version: Any,
    reason: str,
    rule_id: str,
) -> PolicyDecision:
    return PolicyDecision(
        decision=DENIED,
        user_id=user_id if isinstance(user_id, str) else None,
        tenant_id=tenant_id if isinstance(tenant_id, str) else None,
        tool_name=tool_name if isinstance(tool_name, str) else None,
        tool_version=tool_version if isinstance(tool_version, str) else None,
        reason=reason,
        policy_rule_id=rule_id,
        requires_confirmation=False,
    )


class PolicyEngine:
    """Small deterministic evaluator over a static server-owned policy."""

    def __init__(
        self,
        policy_path: Path | str = DEFAULT_POLICY_PATH,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.registry = registry or get_registry()
        policy = _load_yaml(Path(policy_path))
        self._users = _validate_policy(policy, self.registry)

    def evaluate(self, request: Mapping[str, Any]) -> PolicyDecision:
        """Evaluate one authenticated tool request. Fails closed on any ambiguity."""
        if not isinstance(request, dict):
            return _denied(None, None, None, None, "malformed_request", RULE_MALFORMED_REQUEST)

        user_id = request.get("user_id")
        tenant_id = request.get("tenant_id")
        tool_name = request.get("tool_name")
        requested_version = request.get("tool_version")
        arguments = request.get("arguments") or {}

        if not isinstance(user_id, str) or not user_id:
            return _denied(user_id, tenant_id, tool_name, requested_version, "malformed_request", RULE_MALFORMED_REQUEST)
        if not isinstance(tenant_id, str) or not tenant_id:
            return _denied(user_id, tenant_id, tool_name, requested_version, "malformed_request", RULE_MALFORMED_REQUEST)
        if not isinstance(tool_name, str) or not tool_name:
            return _denied(user_id, tenant_id, tool_name, requested_version, "malformed_request", RULE_MALFORMED_REQUEST)
        if not isinstance(requested_version, str) or not requested_version:
            return _denied(user_id, tenant_id, tool_name, requested_version, "unsupported_tool_version", RULE_TOOL_VERSION_MISMATCH)

        policy_user = self._users.get(user_id)
        if policy_user is None:
            return _denied(user_id, tenant_id, tool_name, requested_version, "unknown_user", RULE_UNKNOWN_USER)
        if policy_user.tenant_id != tenant_id:
            return _denied(user_id, tenant_id, tool_name, requested_version, "unknown_tenant", RULE_UNKNOWN_TENANT)

        try:
            contract = self.registry.get(tool_name)
        except KeyError:
            return _denied(user_id, tenant_id, tool_name, requested_version, "tool_not_found", RULE_TOOL_NOT_FOUND)

        if contract["tool_version"] != requested_version:
            return _denied(
                user_id,
                tenant_id,
                tool_name,
                contract["tool_version"],
                "unsupported_tool_version",
                RULE_TOOL_VERSION_MISMATCH,
            )
        if tool_name in policy_user.denied_tools:
            return _denied(user_id, tenant_id, tool_name, contract["tool_version"], "explicit_deny", RULE_EXPLICIT_DENY)
        if tool_name not in policy_user.allowed_tools:
            return _denied(user_id, tenant_id, tool_name, contract["tool_version"], "permission_denied", RULE_PERMISSION_DENIED)
        # R3 large-quantity guard: any line with quantity >= 1000 requires manager
        # escalation and is denied at the policy layer before reaching ERP.
        if tool_name == "sales.order.create":
            lines = (arguments or {}).get("lines") or []
            try:
                for line in lines:
                    qty = line.get("quantity", 0)
                    if isinstance(qty, (int, float)) and qty >= 1000:
                        return _denied(user_id, tenant_id, tool_name, contract["tool_version"],
                                       "large_quantity_requires_manager", "RULE_LARGE_QUANTITY")
            except (TypeError, AttributeError):
                pass
        if contract["readOnly"] is False or contract["requiresConfirmation"] is True:
            return PolicyDecision(
                decision=CONFIRMATION_REQUIRED,
                user_id=user_id,
                tenant_id=tenant_id,
                tool_name=tool_name,
                tool_version=contract["tool_version"],
                reason="confirmation_required",
                policy_rule_id=RULE_CONFIRMATION_REQUIRED,
                requires_confirmation=True,
            )
        return PolicyDecision(
            decision=ALLOWED,
            user_id=user_id,
            tenant_id=tenant_id,
            tool_name=tool_name,
            tool_version=contract["tool_version"],
            reason="allowed",
            policy_rule_id=RULE_ALLOWED,
            requires_confirmation=False,
        )