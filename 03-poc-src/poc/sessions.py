"""In-process chat sessions: bounded multi-turn memory for the cockpit.

Conversation state is *presentation* state, not authority: it feeds the model
context, it can never change a policy decision. That keeps this module
deliberately dumb and safe:

- bounded turns per session (the harness trims the oldest pair, never truncates
  a tool result mid-turn),
- bounded number of sessions (LRU eviction) so a long-lived server cannot grow
  without limit,
- no PII beyond the utterances themselves, no ERP payload bodies (we keep a short
  headline so the sidebar can label the turn),
- TTL pruning for idle conversations.

Sessions live in memory only: a restart wipes them by design (the durable record
is the audit chain, not the chat).
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

DEFAULT_MAX_TURNS = 24          # 12 user/assistant exchanges kept per session
DEFAULT_MAX_SESSIONS = 64
DEFAULT_IDLE_TTL_SECONDS = 60 * 60 * 6


@dataclass
class Turn:
    role: str                    # "user" | "assistant"
    content: str
    created_at: float = field(default_factory=time.time)
    tool: str | None = None
    status: str | None = None
    headline: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
            "tool": self.tool,
            "status": self.status,
            "headline": self.headline,
            "meta": dict(self.meta),
        }


@dataclass
class Session:
    session_id: str
    user_id: str
    tenant_id: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    turns: list[Turn] = field(default_factory=list)
    title: str = "محادثة جديدة"
    pending_proposal_id: str | None = None

    @property
    def message_count(self) -> int:
        return len(self.turns)

    def to_dict(self, *, turns: int = 12) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turns": len(self.turns),
            "pending_proposal_id": self.pending_proposal_id,
            "recent": [turn.to_dict() for turn in self.turns[-turns:]],
        }


class SessionStore:
    """Thread-safe LRU store of chat sessions with bounded history."""

    def __init__(
        self,
        *,
        max_turns: int = DEFAULT_MAX_TURNS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        idle_ttl_seconds: float = DEFAULT_IDLE_TTL_SECONDS,
    ) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self._max_turns = max(2, max_turns)
        self._max_sessions = max(1, max_sessions)
        self._idle_ttl = idle_ttl_seconds

    # --- lifecycle ----------------------------------------------------------
    def get_or_create(self, session_id: str | None, *, user_id: str = "", tenant_id: str = "") -> Session:
        with self._lock:
            self._prune_locked()
            if session_id and session_id in self._sessions:
                session = self._sessions.pop(session_id)  # LRU touch
                self._sessions[session_id] = session
                session.updated_at = time.time()
                return session
            new_id = session_id or f"sess_{uuid.uuid4().hex[:12]}"
            session = Session(session_id=new_id, user_id=user_id, tenant_id=tenant_id)
            self._sessions[new_id] = session
            while len(self._sessions) > self._max_sessions:
                self._sessions.pop(next(iter(self._sessions)))
            return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def clear(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session.turns.clear()
            session.title = "محادثة جديدة"
            session.pending_proposal_id = None
            return True

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def list_sessions(self, *, user_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            self._prune_locked()
            rows = [session for session in self._sessions.values() if user_id is None or session.user_id == user_id]
        rows.sort(key=lambda session: session.updated_at, reverse=True)
        return [session.to_dict(turns=4) for session in rows[:limit]]

    # --- content ------------------------------------------------------------
    def append(
        self,
        session_id: str | None,
        role: str,
        content: str,
        *,
        user_id: str = "",
        tenant_id: str = "",
        tool: str | None = None,
        status: str | None = None,
        headline: str = "",
        meta: dict[str, Any] | None = None,
    ) -> Session:
        with self._lock:
            session = self.get_or_create(session_id, user_id=user_id, tenant_id=tenant_id)
            session.turns.append(
                Turn(role=role, content=content, tool=tool, status=status, headline=headline, meta=dict(meta or {}))
            )
            if len(session.turns) > self._max_turns:
                # Drop from the oldest complete pair so the newest turn is never orphaned.
                drop = len(session.turns) - self._max_turns
                while drop > 0 and session.turns and session.turns[0].role == "assistant":
                    session.turns.pop(0)
                    drop -= 1
                session.turns = session.turns[drop:] if drop > 0 else session.turns
            if role == "user" and (session.title in ("", "محادثة جديدة")):
                session.title = (content or "")[:48] or session.title
            session.updated_at = time.time()
            return session

    def history(self, session_id: str | None, *, limit: int | None = None) -> list[dict[str, str]]:
        """Provider-agnostic history as ``[{role, content}]`` pairs, oldest first."""
        if not session_id:
            return []
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return []
            turns = [turn for turn in session.turns if turn.content]
        if limit:
            turns = turns[-max(1, int(limit)):]
        return [{"role": turn.role, "content": turn.content} for turn in turns]

    def set_pending_proposal(self, session_id: str | None, proposal_id: str | None) -> None:
        with self._lock:
            session = self._sessions.get(session_id or "")
            if session is not None:
                session.pending_proposal_id = proposal_id

    def rename(self, session_id: str, title: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session.title = (title or "")[:80] or session.title
            return True

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "sessions": len(self._sessions),
                "turns": sum(len(session.turns) for session in self._sessions.values()),
                "max_turns_per_session": self._max_turns,
                "max_sessions": self._max_sessions,
            }

    # --- internals ----------------------------------------------------------
    def _prune_locked(self, *, now: float | None = None) -> int:
        if self._idle_ttl <= 0:
            return 0
        now = now if now is not None else time.time()
        stale = [key for key, session in self._sessions.items() if now - session.updated_at > self._idle_ttl and session is not None]
        for key in stale:
            self._sessions.pop(key, None)
        return len(stale)


_SHARED: SessionStore | None = None
_SHARED_LOCK = threading.Lock()


def get_session_store() -> SessionStore:
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = SessionStore()
    return _SHARED


def set_session_store(store: SessionStore | None) -> None:
    global _SHARED
    with _SHARED_LOCK:
        _SHARED = store


__all__ = ["Session", "SessionStore", "Turn", "get_session_store", "set_session_store"]
