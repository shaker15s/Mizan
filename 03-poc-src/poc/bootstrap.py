"""Server-side composition for the POC CLI boundary."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

from poc.agent_runtime import AgentRuntime
from poc.authz import PolicyEngine
from poc.db.init import DEFAULT_DB_PATH, initialize
from poc.gateway import ToolGateway
from poc.llm_client import LLMClientProtocol, build_llm_client
from poc.odoo_client import OdooConfig, OdooJSON2Client
from poc.tool_contracts import get_registry

SRC_ROOT = Path(__file__).resolve().parents[1]

_ODOO_API_KEY_VARS = {
    "sales_user@test": "ODOO_API_KEY_SALES_USER",
    "readonly_user@test": "ODOO_API_KEY_READONLY_USER",
    "no_access_user@test": "ODOO_API_KEY_NO_ACCESS_USER",
}


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} must be configured in the environment or .env file.")
    return value


def _build_odoo_client(user_id: str, tenant_id: str):
    """Build the user-specific ERP client inside the server-owned boundary."""
    api_key_variable = _ODOO_API_KEY_VARS.get(user_id)
    if api_key_variable is None:
        raise ValueError("No ERP credentials are configured for this POC user.")
    config = OdooConfig(
        url=_required_env("ODOO_URL"),
        database=_required_env("ODOO_DATABASE"),
        api_key=_required_env(api_key_variable),
    )
    return OdooJSON2Client(config)


def build_llm_client_from_settings(settings=None) -> LLMClientProtocol:
    """Build the provider client from the runtime settings (not just env).

    Called at startup and again whenever the settings panel bumps its version,
    so switching model/provider/temperature is a hot swap, not a restart.
    """
    settings = settings or get_settings()
    provider = str(settings.get("model.provider", "anthropic")).strip().lower()
    model = str(settings.get("model.name", "") or "")
    base_url = str(settings.get("model.base_url", "") or "").strip() or None
    api_key = settings.secret("model.api_key") or None
    if provider == "openai_compatible" and not base_url:
        base_url = os.environ.get("LLM_BASE_URL") or "https://api.openai.com/v1"
    force = str(settings.get("model.force_tool_choice", "off") or "off")
    return build_llm_client(
        model=model or None,
        provider=provider if base_url or provider != "anthropic" else (os.environ.get("LLM_PROVIDER") or "anthropic"),
        temperature=settings.float_of("model.temperature"),
        max_tokens=settings.int_of("model.max_tokens"),
        timeout=settings.float_of("model.timeout_seconds"),
        retries=settings.int_of("model.retries"),
        force_tool_choice=None if force in ("", "off") else force,
        base_url=base_url,
        api_key=api_key,
    )


def build_runtime(
    *,
    user_id: str | None = None,
    tenant_id: str | None = None,
    db_path: Path | str | None = None,
    llm_client: LLMClientProtocol | None = None,
    odoo_client_factory: Callable[[str, str], object] | None = None,
    settings=None,
) -> AgentRuntime:
    """Construct the agent and all server-owned boundaries."""
    load_dotenv(SRC_ROOT / ".env")
    settings = settings or get_settings()
    resolved_user_id = user_id or os.environ.get("POC_USER_ID", "sales_user@test")
    resolved_tenant_id = tenant_id or os.environ.get("TENANT_ID", "poc_tenant_001")
    if user_id is None and "POC_USER_ID" not in os.environ:
        raise ValueError("POC_USER_ID must be configured by the server owner.")
    if db_path is None:
        configured_path = os.environ.get("GATEWAY_DB_PATH", str(DEFAULT_DB_PATH))
        db_path = Path(configured_path)
        if not db_path.is_absolute():
            db_path = SRC_ROOT / db_path
    initialize(db_path)
    resolved_factory = odoo_client_factory or _build_odoo_client
    if settings.bool_of("features.simulated_llm") and llm_client is None:
        from poc.simulated_llm import SimulatedLLMClient

        resolved_client: LLMClientProtocol = SimulatedLLMClient()
    else:
        resolved_client = llm_client or build_llm_client_from_settings(settings)
    return AgentRuntime(
        llm_client=resolved_client,
        gateway=ToolGateway(
            registry=get_registry(),
            policy_engine=PolicyEngine(),
            db_path=db_path,
            confirm_ttl_seconds=settings.int_of("governance.confirm_ttl_seconds") or None,
        ),
        registry=get_registry(),
        user_id=resolved_user_id,
        tenant_id=resolved_tenant_id,
        odoo_client_factory=resolved_factory,
        settings=settings,
        dialect=str(settings.get("agent.dialect", "ar-EG")),
        response_style=str(settings.get("agent.response_style", "balanced")),
        history_turns=settings.int_of("agent.memory_turns"),
        enable_narrative=settings.bool_of("agent.narrative_composer"),
        max_repair_turns=1 if settings.bool_of("agent.narrative_composer") else 0,
    )
