"""Tests for Phase 11 Storage abstraction and Phase 12 deploy hardening."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from poc import backup, deploy, storage


def test_sqlite_storage_opens_all_repositories(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    s = storage.open_storage(db)
    try:
        assert s.audit is not None
        assert s.proposals is not None
        assert s.idempotency is not None
        assert s.sessions is not None
        # schema_migrations table exists (idempotent).
        assert db.exists()
    finally:
        s.close()


def test_backup_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    s = storage.open_storage(db)
    s.close()
    bk = tmp_path / "bk"
    res = backup.snapshot_sqlite(db, bk)
    assert res.path.exists()
    assert res.bytes_written > 0
    sidecar = res.path.with_suffix(res.path.suffix + ".sha256")
    assert sidecar.exists()
    assert len(backup.list_backups(bk)) == 1
    # Refuse to restore over existing db without force.
    with pytest.raises(FileExistsError):
        backup.restore_sqlite(res.path, db)
    # Force-restore works.
    restored = backup.restore_sqlite(res.path, tmp_path / "m2.db", force=True, verify_checksum=True)
    assert restored.exists()


def test_secrets_dev_is_permissive() -> None:
    rep = deploy.verify_secrets("dev", environ={})
    assert rep.ok is True
    assert rep.missing == ()


def test_secrets_prod_requires_keys() -> None:
    rep = deploy.verify_secrets("production", environ={})
    assert rep.ok is False
    assert "MIZAN_SESSION_SECRET" in rep.missing
    # placeholder value rejected
    rep2 = deploy.verify_secrets("production", environ={
        "MIZAN_SESSION_SECRET": "change-me",
        "MIZAN_SIGNING_KEY": "real",
    })
    assert rep2.ok is False
    assert "MIZAN_SESSION_SECRET" in rep2.placeholder


def test_readiness_requires_all_checks() -> None:
    ok = deploy.readiness_check(db_reachable=True, odoo_reachable=True, llm_reachable=True)
    assert ok.ready is True
    bad = deploy.readiness_check(db_reachable=True, odoo_reachable=False, llm_reachable=True)
    assert bad.ready is False


def test_generate_secret_and_incident_id() -> None:
    assert len(deploy.generate_session_secret()) >= 32
    assert deploy.new_incident_id().startswith("INC-")
