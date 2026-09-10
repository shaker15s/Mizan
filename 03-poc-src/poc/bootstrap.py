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


def build_runtime(
    *,
    user_id: str | None = None,
    tenant_id: str | None = None,
    db_path: Path | str | None = None,
    llm_client: LLMClientProtocol | None = None,
    odoo_client_factory: Callable[[str, str], object] | None = None,
) -> AgentRuntime:
    """Construct the agent and all server-owned boundaries."""
    load_dotenv(SRC_ROOT / ".env")
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
    return AgentRuntime(
        llm_client=llm_client or build_llm_client(),
        gateway=ToolGateway(
            registry=get_registry(),
            policy_engine=PolicyEngine(),
            db_path=db_path,
        ),
        registry=get_registry(),
        user_id=resolved_user_id,
        tenant_id=resolved_tenant_id,
        odoo_client_factory=resolved_factory,
    )
