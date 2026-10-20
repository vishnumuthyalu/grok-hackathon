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
            return Session.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            return Session(updated_at=_now())


def save(session: Session) -> Session:
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        session.revision += 1
        session.updated_at = _now()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(session.model_dump_json(), encoding="utf-8")
        os.replace(tmp, path)
    return session


def reset() -> Session:
    """Replace the shared session with an empty one.

    The revision keeps climbing. An open browser ignores a file whose
    revision went backwards, so a cleared session must still look newer.
    """
    path = session_path()
    previous = 0
    with _lock:
        if path.exists():
            try:
                previous = Session.model_validate_json(path.read_text(encoding="utf-8")).revision
            except ValueError:
                previous = 0
            path.unlink()
    fresh = Session()
    fresh.revision = previous
    return save(fresh)
