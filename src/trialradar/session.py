"""JSON session shared by the web UI and the MCP server, including across processes."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

from trialradar.models import Session
from trialradar.paths import session_path

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load() -> Session:
    path = session_path()
    with _lock:
        if not path.exists():
            return Session(updated_at=_now())
        try:
            return Session.model_validate_json(path.read_text())
        except ValueError:
            return Session(updated_at=_now())


def save(session: Session) -> Session:
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        session.revision += 1
        session.updated_at = _now()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(session.model_dump_json())
        os.replace(tmp, path)
    return session


def reset() -> Session:
    path = session_path()
    with _lock:
        if path.exists():
            path.unlink()
    return save(Session())
