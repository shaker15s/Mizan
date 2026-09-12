"""Unit tests for the enterprise CircuitBreaker in poc.odoo_client."""

from __future__ import annotations

import httpx
import pytest

from poc.odoo_client import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    OdooConfig,
    OdooJSON2Client,
    OdooTimeoutError,
)

CONFIG = OdooConfig(
    url="http://localhost:8069",
    database="poc_test",
    api_key="unit-test-key",
    timeout_seconds=0.05,
)


def test_circuit_breaker_starts_closed():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.2)
    assert cb.state == "CLOSED"
    assert cb.can_execute() is True
    status = cb.get_status()
    assert status["state"] == "CLOSED"
    assert status["healthy"] is True
    assert status["failure_count"] == 0


def test_circuit_breaker_trips_after_threshold():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.5)
    cb.record_failure()
    assert cb.state == "CLOSED"
    assert cb.failure_count == 1

    cb.record_failure()
    assert cb.state == "CLOSED"
    assert cb.failure_count == 2

    cb.record_failure()
    assert cb.state == "OPEN"
    assert cb.failure_count == 3
    assert cb.can_execute() is False
    assert cb.get_status()["healthy"] is False


def test_circuit_breaker_recovers_after_timeout():
    import time
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "OPEN"
    assert cb.can_execute() is False

    time.sleep(0.06)
    assert cb.can_execute() is True
    assert cb.state == "HALF_OPEN"

    cb.record_success()
    assert cb.state == "CLOSED"
    assert cb.failure_count == 0


def test_odoo_client_circuit_breaker_blocks_requests(monkeypatch):
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=10.0)
    c = OdooJSON2Client(CONFIG, circuit_breaker=cb)

    def fake_post(url, **kwargs):
        raise httpx.TimeoutException("down")

    monkeypatch.setattr(httpx, "post", fake_post)

    # 1st failure
    with pytest.raises(OdooTimeoutError):
        c.read("res.partner", [1], ["name"])
    assert cb.failure_count == 1

    # 2nd failure - trips breaker
    with pytest.raises(OdooTimeoutError):
        c.read("res.partner", [1], ["name"])
    assert cb.state == "OPEN"

    # 3rd attempt is rejected immediately by the circuit breaker without hitting httpx
    with pytest.raises(CircuitBreakerOpenError) as exc:
        c.read("res.partner", [1], ["name"])
    assert "Circuit Breaker is OPEN" in str(exc.value)
    assert exc.value.status == 503
