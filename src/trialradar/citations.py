"""Citations and source verification for data shown from ClinicalTrials.gov.

Everything here is deterministic string and date checks. No language model is
involved. The score says how well what TrialRadar displays traces back to the
ClinicalTrials.gov record. It is NOT a clinical-quality or trial-quality score.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from trialradar.models import Trial

NCT_RE = re.compile(r"^NCT\d{8}$")
STUDY_URL = "https://clinicaltrials.gov/study/{nct}"
API_URL = "https://clinicaltrials.gov/api/v2/studies/{nct}"
FRESH_DAYS = 365

SECTION_ANCHORS = {
    "Eligibility Criteria": "#participation-criteria",
    "Locations": "#contacts-and-locations",
}

SCORE_EXPLANATION = (
    "Source-verification score (0-100), computed by code, not by AI: "
    "60 pts = share of displayed eligibility quotes found word-for-word in the "
    "ClinicalTrials.gov record; 15 pts = valid NCT id; 15 pts = record came from "
    "the ClinicalTrials.gov API (live or recorded demo cache); 10 pts = record "
    f"updated on ClinicalTrials.gov within the last {FRESH_DAYS} days. "
    "It checks that sources are real and quoted accurately. It is not a clinical-quality "
    "score: it does not rate the trial "
    "or the eligibility judgment."
)


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def study_url(nct_id: str) -> str:
    return STUDY_URL.format(nct=nct_id)


def api_url(nct_id: str) -> str:
    return API_URL.format(nct=nct_id)


def _sections(trial: Trial) -> list[tuple[str, str]]:
    age_sex = " ".join(
        part for part in (
            f"Minimum age {trial.minimum_age}" if trial.minimum_age else "",
            f"Maximum age {trial.maximum_age}" if trial.maximum_age else "",
            f"Sex {trial.sex}" if trial.sex else "",
        ) if part
    )
    return [
        ("Eligibility Criteria", trial.eligibility_criteria or ""),
        ("Conditions", " | ".join(trial.conditions)),
        ("Title", " | ".join(filter(None, [trial.brief_title, trial.official_title]))),
        ("Interventions", " | ".join(trial.interventions)),
        ("Eligibility (age / sex)", age_sex),
    ]


def cite_quote(quote: str | None, trial: Trial) -> dict[str, Any]:
    """Find which section of the stored record contains this quote verbatim.

    Whitespace is collapsed on both sides (the record uses line breaks and
    bullets); otherwise the match is exact and case-sensitive. A quote that
    TrialRadar shortened with an ellipsis is checked by its prefix.
    """
    raw = _norm(quote)
    probe = re.sub(r"(\u2026|\.\.\.)$", "", raw).strip()
    base = study_url(trial.nct_id)
    if len(probe) >= 4:
        for section, text in _sections(trial):
            if text and probe in _norm(text):
                return {
                    "nct_id": trial.nct_id,
                    "section": section,
                    "verified": True,
                    "label": "Verified quote",
                    "url": base + SECTION_ANCHORS.get(section, ""),
                }
    return {
        "nct_id": trial.nct_id,
        "section": None,
        "verified": False,
        "label": "Not found in source",
        "url": base,
    }


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%B %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _pretty(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return iso
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def provenance(trial: Trial, *, demo_cache: bool, retrieved_at: str | None, recorded_at: str | None) -> dict[str, Any]:
    if demo_cache:
        when = recorded_at or retrieved_at
        retrieval = f"Recorded demo cache of ClinicalTrials.gov, recorded at {_pretty(when)}" if when else "Recorded demo cache of ClinicalTrials.gov"
    else:
        when = retrieved_at
        retrieval = f"Retrieved live from ClinicalTrials.gov at {_pretty(when)}" if when else "Retrieved live from ClinicalTrials.gov"
    return {
        "source": "ClinicalTrials.gov",
        "nct_id": trial.nct_id,
        "study_url": study_url(trial.nct_id),
        "api_url": api_url(trial.nct_id),
        "last_update": trial.last_update,
        "retrieved_at": when,
        "retrieval_mode": "demo_cache" if demo_cache else "live",
        "retrieval_label": retrieval,
    }


def verification(trial: Trial, citations: list[dict[str, Any]], *, today: date | None = None) -> dict[str, Any]:
    today = today or datetime.now(timezone.utc).date()
    id_ok = bool(NCT_RE.match(trial.nct_id or "")) and trial.url.rstrip("/").endswith(trial.nct_id)
    record_ok = bool(trial.eligibility_criteria.strip()) and bool(trial.brief_title)
    updated = _parse_day(trial.last_update)
    age_days = (today - updated).days if updated else None
    fresh = age_days is not None and 0 <= age_days <= FRESH_DAYS
    total = len(citations)
    verified = sum(1 for item in citations if item.get("verified"))
    points = (15 if id_ok else 0) + (15 if record_ok else 0) + (10 if fresh else 0)
    if total:
        points += round(60 * verified / total)
        score = points
    else:
        # Not checked yet: no quotes on screen, so scale the other checks to 100.
        score = round(points * 100 / 40)
    if score >= 90:
        label = "Source verified"
    elif score >= 60:
        label = "Partly verified"
    else:
        label = "Check source"
    return {
        "score": score,
        "label": label,
        "quotes_total": total,
        "quotes_verified": verified,
        "nct_id_valid": id_ok,
        "record_present": record_ok,
        "last_update_days": age_days,
        "fresh": fresh,
        "method": "deterministic",
        "explanation": SCORE_EXPLANATION,
        "summary": (
            f"{verified}/{total} eligibility quotes verbatim in record"
            if total else "No eligibility quotes checked yet"
        ) + f" · NCT id {'valid' if id_ok else 'INVALID'}"
        + f" · record {'present' if record_ok else 'MISSING'}"
        + (f" · updated {age_days} days ago" if age_days is not None else " · last update unknown"),
    }


def sources_block(trial: dict) -> list[str]:
    """Plain-text Sources section for drafts."""
    prov = trial.get("provenance") or {}
    ver = trial.get("verification") or {}
    nct = trial.get("nct_id")
    lines = [
        "Sources (ClinicalTrials.gov):",
        f"[1] {nct} study record: {prov.get('study_url') or trial.get('url')}",
        f"[2] {nct} API v2 record: {prov.get('api_url') or api_url(nct)}",
        f"Record last updated on ClinicalTrials.gov: {prov.get('last_update') or 'not listed'}",
    ]
    if prov.get("retrieval_label"):
        lines.append(prov["retrieval_label"] + ".")
    if ver:
        lines.append(
            f"Source-verification score: {ver.get('score')}/100 ({ver.get('label')}) — "
            f"{ver.get('quotes_verified')}/{ver.get('quotes_total')} eligibility quotes found word-for-word "
            "in the record. Computed by code; not a clinical-quality score."
        )
    return lines
