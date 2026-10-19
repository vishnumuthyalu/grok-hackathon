"""Recorded results for the three prepared demo patients."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from trialradar.geo import normalize_place
from trialradar.models import Patient, Trial, TrialCheck
from trialradar.paths import demo_cache_path


def cancer_family(cancer: str | None) -> str:
    text = (cancer or "").lower()
    if any(token in text for token in ("lung", "nsclc")):
        return "lung"
    if "breast" in text:
        return "breast"
    if any(token in text for token in ("colon", "colorectal", "rectal")):
        return "crc"
    return "other"


def marker_key(patient: Patient) -> str:
    blob = patient.marker_text()
    if "g12c" in blob:
        return "kras_g12c"
    if any(token in blob for token in ("l858r", "exon 19", "ex19del")):
        return "egfr"
    if "egfr" in blob and not re.search(r"none|negative|wild", blob):
        return "egfr"
    if "her2" in blob or "her-2" in blob:
        if any(token in blob for token in ("pos", "+", "amplif", "3")):
            return "her2"
    if any(token in blob for token in ("triple", "tnbc")):
        return "tnbc"
    if re.search(r"\balk\b", blob) and "neg" not in blob:
        return "alk"
    if "msi" in blob:
        return "msi"
    if "braf" in blob:
        return "braf"
    return "other"


def signature(patient: Patient) -> dict[str, str]:
    return {
        "cancer": cancer_family(patient.cancer_type),
        "marker": marker_key(patient),
        "city": normalize_place(patient.city or ""),
    }


def load_cache() -> dict[str, Any] | None:
    path = demo_cache_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def match_patient(patient: Patient) -> dict[str, Any] | None:
    cache = load_cache()
    if not cache:
        return None
    wanted = signature(patient)
    for entry in (cache.get("patients") or {}).values():
        if entry.get("signature") == wanted:
            return entry
    return None


def trials_from_entry(entry: dict[str, Any]) -> list[Trial]:
    return [Trial.model_validate(item) for item in entry.get("trials") or []]


def checks_from_entry(entry: dict[str, Any]) -> dict[str, TrialCheck]:
    checks = {}
    for nct_id, payload in (entry.get("checks") or {}).items():
        check = TrialCheck.model_validate(payload)
        check.from_cache = True
        check.nct_id = nct_id
        checks[nct_id] = check
    return checks


def write_cache(patients: dict[str, dict[str, Any]], *, provider: str) -> None:
    path = demo_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "source": "https://clinicaltrials.gov/api/v2/studies",
        "note": (
            "Trial records are responses from ClinicalTrials.gov. "
            "Rule judgments were produced by the configured provider at record time. "
            "Nothing here was invented."
        ),
        "patients": patients,
    }
    path.write_text(json.dumps(payload))
