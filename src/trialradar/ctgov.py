"""ClinicalTrials.gov API v2 client. Public endpoint, no API key.

Verified against https://clinicaltrials.gov/api/v2/studies (October 2026):
studies[].protocolSection.identificationModule.nctId / briefTitle,
statusModule.overallStatus / lastUpdatePostDateStruct.date,
designModule.studyType / phases,
eligibilityModule.eligibilityCriteria / minimumAge / maximumAge / sex / stdAges,
contactsLocationsModule.locations[].facility / city / state / country / status /
geoPoint.lat / geoPoint.lon,
conditionsModule.conditions, armsInterventionsModule.interventions[].name.
Geo filter: filter.geo=distance(lat,lon,100mi)
Study type filter: filter.advanced=AREA[StudyType]INTERVENTIONAL
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from trialradar.models import Site, Trial

API_URL = "https://clinicaltrials.gov/api/v2/studies"
USER_AGENT = "TrialRadar/0.1 (local educational demo; open-source hackathon)"


class CtGovError(RuntimeError):
    pass


def build_query(patient) -> tuple[str, str | None]:
    """Condition + a single biomarker term. Retried without the term if few hits."""
    cancer = (patient.cancer_type or "").lower()
    markers = patient.marker_text()
    term: str | None = None
    if any(token in cancer for token in ("lung", "nsclc")):
        condition = "non-small cell lung cancer"
        if any(token in markers for token in ("egfr", "l858r", "exon 19", "ex19")):
            term = "EGFR"
        elif "alk" in markers:
            term = "ALK"
        elif "g12c" in markers:
            term = "KRAS G12C"
        elif "kras" in markers:
            term = "KRAS"
    elif "breast" in cancer:
        condition = "breast cancer"
        if any(token in markers for token in ("triple", "tnbc")):
            term = "triple negative"
        elif "her2" in markers or "her-2" in markers:
            term = "HER2-positive"
    elif any(token in cancer for token in ("colon", "colorectal", "rectal")):
        condition = "colorectal cancer"
        if "g12c" in markers:
            term = "KRAS G12C"
        elif "msi" in markers or "microsatellite" in markers:
            term = "microsatellite"
        elif "braf" in markers:
            term = "BRAF"
        elif "kras" in markers:
            term = "KRAS"
    else:
        condition = patient.cancer_type or "solid tumor"
    return condition, term


def search_studies(
    *,
    condition: str,
    term: str | None,
    lat: float,
    lon: float,
    radius_mi: int,
    recruiting_only: bool,
    page_size: int = 20,
    max_studies: int = 30,
) -> tuple[list[dict[str, Any]], int | None]:
    """Return raw study payloads and the API totalCount."""
    params: dict[str, str] = {
        "query.cond": condition,
        "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
        "filter.geo": f"distance({lat},{lon},{int(radius_mi)}mi)",
        "pageSize": str(page_size),
        "countTotal": "true",
    }
    if term:
        params["query.term"] = term
    if recruiting_only:
        params["filter.overallStatus"] = "RECRUITING"
    else:
        params["filter.overallStatus"] = (
            "RECRUITING,NOT_YET_RECRUITING,ENROLLING_BY_INVITATION,ACTIVE_NOT_RECRUITING"
        )
    studies: list[dict[str, Any]] = []
    total: int | None = None
    token: str | None = None
    with httpx.Client(timeout=40, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as client:
        while len(studies) < max_studies:
            page = dict(params)
            if token:
                page["pageToken"] = token
            try:
                response = client.get(API_URL, params=page)
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise CtGovError(f"ClinicalTrials.gov request failed: {exc}") from exc
            if total is None:
                total = payload.get("totalCount")
            batch = payload.get("studies") or []
            studies.extend(batch)
            token = payload.get("nextPageToken")
            if not token or not batch:
                break
    return studies[:max_studies], total


def parse_study(study: dict[str, Any]) -> Trial | None:
    protocol = study.get("protocolSection") or {}
    ident = protocol.get("identificationModule") or {}
    status = protocol.get("statusModule") or {}
    design = protocol.get("designModule") or {}
    elig = protocol.get("eligibilityModule") or {}
    conditions = protocol.get("conditionsModule") or {}
    arms = protocol.get("armsInterventionsModule") or {}
    locations = protocol.get("contactsLocationsModule") or {}
    nct_id = ident.get("nctId")
    if not nct_id:
        return None
    if design.get("studyType") not in (None, "INTERVENTIONAL"):
        return None
    std_ages = list(elig.get("stdAges") or [])
    if std_ages == ["CHILD"]:
        return None
    sites: list[Site] = []
    for loc in locations.get("locations") or []:
        point = loc.get("geoPoint") or {}
        lat = point.get("lat")
        lon = point.get("lon")
        sites.append(
            Site(
                facility=loc.get("facility"),
                city=loc.get("city"),
                state=loc.get("state"),
                country=loc.get("country"),
                status=loc.get("status"),
                lat=float(lat) if lat is not None else None,
                lon=float(lon) if lon is not None else None,
            )
        )
    interventions = [
        item.get("name")
        for item in (arms.get("interventions") or [])
        if item.get("name")
    ]
    posted = (status.get("lastUpdatePostDateStruct") or {}).get("date")
    criteria = elig.get("eligibilityCriteria") or ""
    if len(criteria) > 14000:
        criteria = criteria[:14000]
    return Trial(
        nct_id=nct_id,
        brief_title=ident.get("briefTitle") or nct_id,
        official_title=ident.get("officialTitle"),
        overall_status=status.get("overallStatus") or "UNKNOWN",
        phases=list(design.get("phases") or []),
        study_type=design.get("studyType"),
        conditions=list(conditions.get("conditions") or []),
        interventions=interventions,
        eligibility_criteria=criteria,
        minimum_age=elig.get("minimumAge"),
        maximum_age=elig.get("maximumAge"),
        sex=elig.get("sex"),
        std_ages=std_ages,
        sites=sites,
        url=f"https://clinicaltrials.gov/study/{nct_id}",
        last_update=posted,
    )


def marker_tokens(patient) -> list[str]:
    markers = patient.marker_text()
    tokens: list[str] = []
    if any(item in markers for item in ("egfr", "l858r", "exon 19")):
        tokens += ["egfr", "l858r", "exon 19"]
    if "alk" in markers:
        tokens.append("alk")
    if "g12c" in markers:
        tokens += ["g12c", "kras"]
    elif "kras" in markers:
        tokens.append("kras")
    if "her2" in markers or "her-2" in markers:
        tokens += ["her2", "her-2"]
    if "msi" in markers:
        tokens += ["msi", "microsatellite"]
    if "braf" in markers:
        tokens.append("braf")
    if "triple" in markers or "tnbc" in markers:
        tokens += ["triple negative", "tnbc"]
    return tokens


def retrieval_score(trial: Trial, patient) -> int:
    """Cheap ordering so the trials we spend a check on mention the patient's markers.

    This is not an eligibility judgment. It only sorts real retrieved studies.
    """
    title = " ".join(
        [
            trial.brief_title or "",
            trial.official_title or "",
            " ".join(trial.conditions),
        ]
    ).lower()
    criteria = (trial.eligibility_criteria or "").lower()[:4000]
    score = 0
    for token in marker_tokens(patient):
        if token in title:
            score += 3
        elif token in criteria:
            score += 1
    return score


def fetch_radius() -> int:
    try:
        return max(25, int(os.environ.get("FETCH_RADIUS_MI", "250")))
    except ValueError:
        return 250
