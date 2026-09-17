"""Gateway read-path Arabic normalization fallback ladder tests.

Proves the bounded ladder (raw -> normalized -> hamza variant) resolves
bare-typed and hamza-typed Arabic queries against raw stored names,
enforces the search limit cap, and stays bounded.
"""

import pytest

from poc.gateway import ToolGateway, _query_variants
from poc.tool_contracts import get_registry


class FakeOdooSearch:
    """Odoo client double that matches queries against raw stored names."""

    def __init__(self, records):
        self._records = records
        self.calls = []

    def search_read(self, model, domain, fields, limit):
        self.calls.append(domain)
        query = domain[0][2]
        hits = [r for r in self._records if query.lower() in r["name"].lower()]
        if limit is not None:
            hits = hits[:limit]
        return hits

    def read(self, model, ids, fields):
        return [r for r in self._records if r["id"] in ids]


@pytest.fixture
def gateway(tmp_path):
    return ToolGateway(db_path=tmp_path / "gw.db")


def _request(tool, args):
    return {
        "request_id": "req-1",
        "user_id": "sales_user@test",
        "tenant_id": "poc_tenant_001",
        "tool_name": tool,
        "tool_version": get_registry().get(tool)["tool_version"],
        "arguments": args,
    }


_STORED = [
    {"id": 45, "name": "أحمد حسن", "email": None, "phone": None},
    {"id": 42, "name": "محمد أحمد", "email": None, "phone": None},
]


def test_raw_query_hits_in_one_call(gateway):
    client = FakeOdooSearch(_STORED)
    result = gateway.handle_request(_request("customer.search", {"query": "أحمد حسن"}), odoo_client=client)
    assert result.status == "accepted"
    assert len(client.calls) == 1
    assert result.result["count"] == 1


def test_bare_alef_falls_through_ladder_to_hamza_stored(gateway):
    client = FakeOdooSearch(_STORED)
    result = gateway.handle_request(_request("customer.search", {"query": "احمد حسن"}), odoo_client=client)
    assert result.status == "accepted"
    assert result.result["count"] == 1
    assert len(client.calls) <= 3  # ladder: raw -> normalized -> hamza variant


def test_hamza_typed_falls_through_ladder_to_bare_stored(gateway):
    client = FakeOdooSearch([{"id": 50, "name": "احمد سيد"}])
    result = gateway.handle_request(_request("customer.search", {"query": "أحمد سيد"}), odoo_client=client)
    assert result.status == "accepted"
    assert result.result["count"] == 1
    assert len(client.calls) <= 3


def test_limit_cap_enforced(gateway):
    records = [{"id": i, "name": f"عميل {i}"} for i in range(30)]
    client = FakeOdooSearch(records)
    result = gateway.handle_request(_request("customer.search", {"query": "عميل"}), odoo_client=client)
    assert result.result["count"] == 20  # limit_cap
    assert len(client.calls) == 1


def test_no_results_is_legitimate_success(gateway):
    client = FakeOdooSearch(_STORED)
    result = gateway.handle_request(_request("customer.search", {"query": "شخص غير موجود"}), odoo_client=client)
    assert result.status == "accepted"
    assert result.result["count"] == 0
    assert len(client.calls) == 1  # no orthographic variants -> ladder has nothing to try


def test_query_variants_bounded():
    assert _query_variants("احمد") == ["أحمد"]
    assert _query_variants("شركة النيل") == ["شركه النيل"]
    assert _query_variants("أحمد") == ["احمد"]
    assert _query_variants("النيل") == []  # already normalized + article "ال" never hamza-ized
