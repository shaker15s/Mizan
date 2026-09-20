"""Phase 12 — Backups and rollback helpers.

At this stage Mizan's single source of persistent truth for the POC/pilot
is the SQLite gateway database (audit ledger + proposals + idempotency).
This module provides an atomic snapshot + restore that:
  * uses SQLite's online backup API so snapshots can be taken live without
    stopping the server;
  * writes into a timestamped, checksummed file under a configured dir;
  * refuses to restore over a non-empty existing database unless forced
    (fail-closed).
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

LOGGER = logging.getLogger("mizan.backup")


@dataclass(frozen=True)
class BackupResult:
    path: Path
    bytes_written: int
    sha256: str
    taken_at: str


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_sqlite(db_path: Path | str, backup_dir: Path | str) -> BackupResult:
    src = Path(db_path)
    dest_dir = Path(backup_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        raise FileNotFoundError(f"database not found: {src}")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = dest_dir / f"mizan-{ts}.db"
    # Use SQLite online backup API — safe on a live DB.
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(target))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    digest = _sha256_file(target)
    # Write a sidecar .sha256 so restores can verify integrity.
    target.with_suffix(target.suffix + ".sha256").write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    size = target.stat().st_size
    LOGGER.info("snapshot written: %s (%d bytes, sha256=%s)", target, size, digest[:12])
    return BackupResult(path=target, bytes_written=size, sha256=digest, taken_at=ts)


def restore_sqlite(backup_path: Path | str, target_db_path: Path | str, *, force: bool = False,
                   verify_checksum: bool = True) -> Path:
    src = Path(backup_path)
    dest = Path(target_db_path)
    if not src.exists():
        raise FileNotFoundError(f"backup not found: {src}")
    if verify_checksum:
        sidecar = src.with_suffix(src.suffix + ".sha256")
        if sidecar.exists():
            expected = sidecar.read_text(encoding="utf-8").split()[0]
            actual = _sha256_file(src)
            if expected != actual:
                raise ValueError(f"backup checksum mismatch: {src.name}")
    if dest.exists() and not force:
        raise FileExistsError(
            f"refusing to restore over existing database {dest}; pass force=True or move it aside"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    LOGGER.warning("restored backup: %s -> %s", src, dest)
    return dest


def list_backups(backup_dir: Path | str) -> list[Path]:
    d = Path(backup_dir)
    if not d.exists():
        return []
    return sorted(p for p in d.glob("mizan-*.db") if p.is_file())


__all__ = ["snapshot_sqlite", "restore_sqlite", "list_backups", "BackupResult"]
