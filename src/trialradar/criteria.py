"""Split ClinicalTrials.gov eligibility text into individual rules."""

from __future__ import annotations

import hashlib
import re

from trialradar.models import Kind

_BULLET = re.compile(r"^(?:(\d+)\s*[\.\)]\s+|[\*\u2022\-–]\s+)")
_HEADER = re.compile(r"^(key\s+)?(inclusion|exclusion)\b.*criteria\b", re.I)
_SKIP = re.compile(
    r"^(notes?|age|type of participant.*|disease characteristics|key eligibility criteria)\s*:?\s*$",
    re.I,
)


def _kind_from_header(line: str) -> Kind | None:
    lowered = line.strip().rstrip(":").lower()
    if _HEADER.match(lowered) or lowered in {"inclusion criteria", "exclusion criteria", "key inclusion criteria", "key exclusion criteria"}:
        if "exclusion" in lowered:
            return "exclusion"
        if "inclusion" in lowered:
            return "inclusion"
    if lowered in {"inclusion", "exclusion", "key inclusion", "key exclusion"}:
        return "exclusion" if "exclusion" in lowered else "inclusion"
    return None


def rule_id(kind: str, text: str) -> str:
    digest = hashlib.sha1(f"{kind}:{text}".encode()).hexdigest()[:10]
    return f"{kind[:3]}-{digest}"


def split_criteria(text: str, *, limit: int = 36) -> list[tuple[str, str, str]]:
    """Return (id, kind, text) rules in source order.

    Sub-bullets stay attached to the parent bullet so a single requirement
    is not torn into fragments the model would have to guess about.
    """
    if not text or not text.strip():
        return []
    kind: Kind = "inclusion"
    rules: list[tuple[str, str, str]] = []
    current: list[str] = []

    def flush() -> None:
        if not current:
            return
        body = " ".join(part.strip() for part in current if part.strip())
        body = re.sub(r"\s+", " ", body).strip(" ;")
        current.clear()
        if len(body) < 8 or _SKIP.match(body):
            return
        rules.append((rule_id(kind, body), kind, body))

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        header = _kind_from_header(line)
        if header and len(line) < 80:
            flush()
            kind = header
            continue
        if _BULLET.match(line):
            flush()
            current.append(_BULLET.sub("", line))
            continue
        # A line that is only a section label ("Age", "Organ function").
        if line.endswith(":") and len(line) < 60 and not _BULLET.match(line):
            header = _kind_from_header(line)
            if header:
                flush()
                kind = header
            continue
        if current:
            current.append(line)
        elif len(line) > 20:
            current.append(line)
    flush()
    return rules[:limit]
