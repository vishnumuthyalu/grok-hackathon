"""One workflow used by the web API and the MCP tools."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from trialradar import DISCLAIMER
from trialradar.cache import checks_from_entry, load_cache, match_patient, signature, trials_from_entry
from trialradar.citations import SCORE_EXPLANATION, cite_quote, provenance, verification
from trialradar.ctgov import (
    CtGovError,
    build_query,
    fetch_radius,
    parse_study,
    retrieval_score,
    search_studies,
)
from trialradar.demo_patients import PREPARED
from trialradar.eligibility import (
    apply_overrides,
    counts,
    phase_allowed,
    questions_from,
    rescore_rules,
    short_text,
    what_ifs_from,
)
from trialradar.extract import extract_from_path, extract_patient_from_text, normalize_patient
from trialradar.geo import geocode, haversine_miles, place_label
from trialradar.llm import get_provider
from trialradar.models import Filters, Patient, Session, Site, Trial
from trialradar.notes import draft_referral, draft_summary
from trialradar.session import load, save

_ANSWER_SEED = {
    "brain_mets": "brain_mets",
    "kidney": "kidney_adequate",
    "prior_io": "prior_immunotherapy",
    "measurable": "measurable_disease",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_answers(patient: Patient, answers: dict[str, str]) -> dict[str, str]:
    seeded = dict(answers)
    for question_id, field in _ANSWER_SEED.items():
        if question_id in seeded:
            continue
        value = getattr(patient, field, None)
        if value in {"yes", "no", "unknown"}:
            seeded[question_id] = value
    return seeded


def _changed(old: Patient | None, new: Patient) -> set[str]:
    if old is None:
        return set(Patient.model_fields)
    changed = set()
    for name in Patient.model_fields:
        if getattr(old, name) != getattr(new, name):
            changed.add(name)
    return changed


def _top_n() -> int:
    try:
        return max(1, int(os.environ.get("CHECK_TOP_N", "5")))
    except ValueError:
        return 5


def _demo_enabled() -> bool:
    return os.environ.get("DEMO_CACHE", "0").strip() in {"1", "true", "yes"}


def refresh_distances(session: Session) -> None:
    if session.origin_lat is None or session.origin_lon is None:
        return
    for trial in session.trials:
        for site in trial.sites:
            if site.lat is None or site.lon is None:
                continue
            site.distance_mi = round(
                haversine_miles(session.origin_lat, session.origin_lon, site.lat, site.lon),
                1,
            )


def _nearest(trial: Trial, recruiting_only: bool) -> Site | None:
    pool = []
    for site in trial.sites:
        if site.distance_mi is None:
            continue
        if recruiting_only and site.status and site.status not in {"RECRUITING", "ENROLLING_BY_INVITATION"}:
            continue
        pool.append(site)
    if not pool:
        return None
    return min(pool, key=lambda site: site.distance_mi if site.distance_mi is not None else 10**9)


def _visible(trial: Trial, filters: Filters) -> bool:
    if filters.recruiting_only and trial.overall_status not in {"RECRUITING", "ENROLLING_BY_INVITATION"}:
        return False
    if not phase_allowed(trial.phases, filters.phases):
        return False
    site = _nearest(trial, filters.recruiting_only)
    if site is None or site.distance_mi is None:
        return False
    return site.distance_mi <= filters.distance_mi


def _phase_label(phases: list[str]) -> str:
    numbers = []
    for phase in phases:
        for digit in ("1", "2", "3", "4"):
            if digit in phase and digit not in numbers:
                numbers.append(digit)
    if not numbers:
        return "Phase —"
    return "Phase " + "/".join(numbers)


def _site_markers(trial: Trial, filters: Filters) -> list[dict[str, Any]]:
    markers = []
    for site in trial.sites:
        if site.lat is None or site.lon is None or site.distance_mi is None:
            continue
        if filters.recruiting_only and site.status and site.status not in {"RECRUITING", "ENROLLING_BY_INVITATION"}:
            continue
        markers.append({
            "lat": site.lat,
            "lon": site.lon,
            "label": place_label(site.city, site.state),
            "facility": site.facility,
            "distance_mi": site.distance_mi,
        })
    markers.sort(key=lambda item: item["distance_mi"])
    return markers[:6]


def _trial_public(trial: Trial, filters: Filters, session: Session | None = None) -> dict[str, Any]:
    site = _nearest(trial, filters.recruiting_only)
    prov = provenance(
        trial,
        demo_cache=bool(session and session.demo_cache),
        retrieved_at=session.retrieved_at if session else None,
        recorded_at=session.source_recorded_at if session else None,
    )
    return {
        "provenance": prov,
        "verification": verification(trial, []),
        "site_citation": None if site is None else {
            "section": "Locations",
            "nct_id": trial.nct_id,
            "url": prov["study_url"] + "#contacts-and-locations",
        },
        "nct_id": trial.nct_id,
        "brief_title": trial.brief_title,
        "overall_status": trial.overall_status,
        "phases": trial.phases,
        "phase_label": _phase_label(trial.phases),
        "conditions": trial.conditions,
        "interventions": trial.interventions[:6],
        "url": trial.url,
        "last_update": trial.last_update,
        "nearest_distance_mi": None if site is None else site.distance_mi,
        "nearest_label": None if site is None else place_label(site.city, site.state),
        "nearest_lat": None if site is None else site.lat,
        "nearest_lon": None if site is None else site.lon,
        "nearest_facility": None if site is None else site.facility,
        "retrieval_score": trial.retrieval_score,
        "sites": _site_markers(trial, filters),
    }


def _rule_public(rule, trial: Trial | None = None) -> dict[str, Any]:
    hypothetical = rule.resolver == "scan_within_days_hypothetical"
    return {
        "citation": cite_quote(rule.text, trial) if trial is not None else None,
        "id": rule.id,
        "text": rule.text,
        "kind": rule.kind,
        "status": rule.status,
        "reason": rule.reason,
        "evidence": rule.evidence,
        "depends_on": rule.depends_on,
        "reversible": rule.reversible,
        "question_id": rule.question_id,
        "boilerplate": rule.boilerplate,
        "hypothetical": hypothetical,
        "resolver": "scan_within_days" if hypothetical else rule.resolver,
    }


def _place_patient(session: Session, patient: Patient) -> str | None:
    origin = geocode(patient.city or "", allow_network=True)
    if origin is None and patient.city:
        return f"Could not place {patient.city}. Try a US city like Austin, TX."
    if origin:
        session.origin_lat, session.origin_lon = origin
        session.origin_label = patient.city
    return None


def set_patient(patient: Patient, *, source: str = "typed") -> Session:
    session = load()
    patient = normalize_patient(patient)
    session.patient = patient
    session.patient_source = source
    session.answers = _seed_answers(patient, {})
    session.what_ifs = {}
    session.trials = []
    session.checks = {}
    session.selected_nct = None
    session.compare_nct = None
    session.referral_note = None
    session.summary_note = None
    session.error = None
    session.warning = None
    session.demo_cache = False
    _place_patient(session, patient)
    return save(session)


def search(*, patient: Patient | None = None, force_live: bool = False) -> dict[str, Any]:
    session = load()
    if patient is not None:
        patient = normalize_patient(patient)
        session.patient = patient
        session.answers = _seed_answers(patient, {})
        session.what_ifs = {}
        session.checks = {}
        session.selected_nct = None
    if session.patient is None or not session.patient.cancer_type or not session.patient.city:
        session.error = "Enter a cancer type and a city before searching."
        save(session)
        return view(session)
    place_error = _place_patient(session, session.patient)
    if place_error:
        session.error = place_error
        save(session)
        return view(session)
    radius = max(session.filters.distance_mi, fetch_radius())
    used_cache = False
    warning = None
    if _demo_enabled() and not force_live:
        entry = match_patient(session.patient)
        if entry:
            session.trials = trials_from_entry(entry)
            # Recorded judgments stay in the cache until check_eligibility.
            # The browser can then show the list first, and the scores on the next step.
            session.checks = {}
            session.total_available = entry.get("total_available")
            session.demo_cache = True
            session.source_label = "Recorded ClinicalTrials.gov results (demo cache)"
            session.source_recorded_at = (load_cache() or {}).get("recorded_at")
            session.llm_mode = entry.get("provider") or "recorded"
            used_cache = True
        else:
            warning = "This patient is not one of the 3 prepared demos. Searching ClinicalTrials.gov live."
    if not used_cache:
        condition, term = build_query(session.patient)
        try:
            raw, total = search_studies(
                condition=condition,
                term=term,
                lat=session.origin_lat,
                lon=session.origin_lon,
                radius_mi=radius,
                recruiting_only=session.filters.recruiting_only,
            )
            if term and len(raw) < 5:
                extra, total2 = search_studies(
                    condition=condition,
                    term=None,
                    lat=session.origin_lat,
                    lon=session.origin_lon,
                    radius_mi=radius,
                    recruiting_only=session.filters.recruiting_only,
                )
                seen = {item.get("protocolSection", {}).get("identificationModule", {}).get("nctId") for item in raw}
                for item in extra:
                    nct = item.get("protocolSection", {}).get("identificationModule", {}).get("nctId")
                    if nct not in seen:
                        raw.append(item)
                total = total2 or total
        except CtGovError as exc:
            session.error = str(exc)
            session.warning = warning
            save(session)
            return view(session)
        trials = []
        for item in raw:
            parsed = parse_study(item)
            if parsed is None:
                continue
            parsed.retrieval_score = retrieval_score(parsed, session.patient)
            trials.append(parsed)
        session.trials = trials
        session.checks = {}
        session.total_available = total
        session.demo_cache = False
        session.source_label = "ClinicalTrials.gov"
        session.source_recorded_at = None
        session.selected_nct = None
    session.fetched_radius_mi = radius
    session.retrieved_at = _now()
    session.referral_note = None
    session.summary_note = None
    session.error = None
    session.warning = warning
    refresh_distances(session)
    _sort_retrieval(session)
    save(session)
    return view(session)


def _sort_retrieval(session: Session) -> None:
    def distance(trial: Trial) -> float:
        site = _nearest(trial, session.filters.recruiting_only)
        return site.distance_mi if site and site.distance_mi is not None else 10**9

    session.trials.sort(key=lambda trial: (-trial.retrieval_score, distance(trial)))


def _visible_ordered(session: Session) -> list[Trial]:
    ordered = [trial for trial in session.trials if _visible(trial, session.filters)]
    return ordered


def check(*, nct_ids: list[str] | None = None, top_n: int | None = None, next_batch: bool = False) -> dict[str, Any]:
    """Check eligibility for trials that do not already have a stored result.

    The default call covers the first top_n visible trials (retrieval order).
    next_batch covers the next top_n that have not been checked. Each trial
    is one provider call. Later edits do not call the provider again.
    """
    session = load()
    if session.patient is None:
        session.error = "Set a patient before checking eligibility."
        save(session)
        return view(session)
    limit = top_n or _top_n()
    ordered = _visible_ordered(session)
    if nct_ids:
        wanted = set(nct_ids)
        targets = [trial for trial in session.trials if trial.nct_id in wanted]
    elif next_batch:
        targets = [trial for trial in ordered if trial.nct_id not in session.checks][:limit]
    else:
        targets = [trial for trial in ordered[:limit] if trial.nct_id not in session.checks]
    adopted: list[str] = []
    if session.demo_cache and session.patient is not None:
        entry = match_patient(session.patient)
        recorded = checks_from_entry(entry) if entry else {}
        for trial in targets:
            if trial.nct_id in session.checks or trial.nct_id not in recorded:
                continue
            session.checks[trial.nct_id] = recorded[trial.nct_id]
            adopted.append(trial.nct_id)
    missing = [trial for trial in targets if trial.nct_id not in session.checks]
    if missing:
        provider, warning = get_provider()
        session.llm_mode = provider.name
        if warning and not session.demo_cache:
            session.warning = warning
        elif warning and session.llm_mode == "stub":
            session.warning = warning
        for trial in missing:
            checked = provider.evaluate(session.patient, trial.eligibility_criteria)
            checked.nct_id = trial.nct_id
            session.checks[trial.nct_id] = checked
    session.error = None
    save(session)
    payload = view(session)
    payload["newly_checked"] = adopted + [trial.nct_id for trial in missing]
    return payload


def rescore(
    *,
    patient: Patient | None = None,
    answers: dict[str, str] | None = None,
    what_ifs: dict[str, bool] | None = None,
    distance_mi: int | None = None,
    phases: list[str] | None = None,
    recruiting_only: bool | None = None,
) -> dict[str, Any]:
    session = load()
    if session.patient is None and patient is None:
        session.error = "Set a patient before rescoring."
        save(session)
        return view(session)
    changed: set[str] = set()
    if patient is not None:
        incoming = normalize_patient(patient)
        changed = _changed(session.patient, incoming)
        session.patient = incoming
        if "city" in changed or session.origin_lat is None:
            place_error = _place_patient(session, incoming)
            if place_error:
                session.error = place_error
                save(session)
                return view(session)
            refresh_distances(session)
    if answers is not None:
        cleaned = {}
        for key, value in answers.items():
            if value in {"yes", "no", "unknown"}:
                cleaned[key] = value
        session.answers = cleaned
    if what_ifs is not None:
        session.what_ifs = {key: bool(value) for key, value in what_ifs.items()}
    if distance_mi is not None:
        session.filters.distance_mi = int(distance_mi)
    if phases is not None:
        session.filters.phases = [str(item) for item in phases]
    if recruiting_only is not None:
        session.filters.recruiting_only = bool(recruiting_only)
    if changed:
        for nct_id, check in session.checks.items():
            check.rules = rescore_rules(check.rules, session.patient, changed)
            session.checks[nct_id] = check
    # Widening past the fetched radius, or turning recruiting off, needs a new search
    # only when we are not serving the demo cache. The cache already holds the fetch.
    needs_refetch = (
        not session.demo_cache
        and session.trials
        and session.filters.distance_mi > session.fetched_radius_mi
    )
    session.error = None
    save(session)
    if needs_refetch:
        return search()
    return view(session)


def select(nct_id: str | None = None, compare_nct: str | None = None) -> dict[str, Any]:
    session = load()
    if nct_id:
        session.selected_nct = nct_id
    if compare_nct is not None:
        session.compare_nct = compare_nct or None
    save(session)
    return view(session)


def draft(nct_id: str | None = None) -> dict[str, Any]:
    session = load()
    payload = view(session)
    chosen = nct_id or session.selected_nct
    row = next((item for item in payload["ranked"] if item["nct_id"] == chosen), None)
    if row is None or session.patient is None:
        payload["notes"] = None
        payload["error"] = payload.get("error") or "Choose a checked trial before drafting notes."
        return payload
    patient = session.patient.model_dump()
    notes = {
        "nct_id": chosen,
        "referral": draft_referral(patient, row, row["rules"]),
        "summary": draft_summary(patient, row, row["rules"]),
        "disclaimer": DISCLAIMER,
        "delivery": "copy_or_download_only",
        "sources": [row["provenance"]["study_url"], row["provenance"]["api_url"]],
        "provenance": row["provenance"],
        "verification": {key: row["verification"][key] for key in ("score", "label", "summary")},
    }
    session.referral_note = notes["referral"]
    session.summary_note = notes["summary"]
    save(session)
    payload = view(session)
    payload["notes"] = notes
    return payload


def extract(*, note_text: str | None = None, report_path: str | None = None, patient: dict | None = None, source: str = "note") -> dict[str, Any]:
    warning = None
    if patient:
        parsed = normalize_patient(Patient.model_validate(patient))
    elif report_path:
        parsed, _text = extract_from_path(__import__("pathlib").Path(report_path))
        source = "photo"
    elif note_text:
        parsed = extract_patient_from_text(note_text)
        source = source or "note"
    else:
        session = load()
        session.error = "Pass note_text, report_path, or a patient object."
        save(session)
        return view(session)
    session = set_patient(parsed, source=source)
    if warning:
        session.warning = warning
        save(session)
    found = view(session)
    found["extracted_patient"] = session.patient.model_dump() if session.patient else None
    found["missing_fields"] = session.patient.missing_fields if session.patient else []
    found["next_step"] = "Call find_trials to retrieve recruiting studies near this patient."
    return found


def load_example(example_id: str, *, check_now: bool = True) -> dict[str, Any]:
    patient = PREPARED.get(example_id)
    if patient is None:
        session = load()
        session.error = f"Unknown example '{example_id}'."
        save(session)
        return view(session)
    # A previous slider, phase, or recruiting toggle must not hide the prepared demo.
    session = load()
    session.filters = Filters()
    save(session)
    if example_id == "blank":
        set_patient(Patient(), source="typed")
        return view(load())
    search(patient=patient.model_copy(deep=True))
    current = load()
    if check_now and not current.checks:
        check(top_n=_top_n())
    return view(load())


def view(session: Session | None = None) -> dict[str, Any]:
    session = session or load()
    filters = session.filters
    ranked = []
    unchecked = []
    for trial in session.trials:
        if not _visible(trial, filters):
            continue
        public = _trial_public(trial, filters, session)
        check = session.checks.get(trial.nct_id)
        if check is None:
            unchecked.append(public)
            continue
        effective = apply_overrides(check.rules, session.answers, session.what_ifs)
        met, failed, unknown = counts(effective)
        base_failures = [
            {"text": rule.text, "reason": rule.reason, "resolver": rule.resolver}
            for rule in check.rules
            if rule.status == "not_met"
        ]
        rules_out = [_rule_public(rule, trial) for rule in effective]
        public["verification"] = verification(trial, [rule["citation"] for rule in rules_out])
        failed_rules = [rule for rule in rules_out if rule["status"] == "not_met"]
        blocking = None
        if failed_rules:
            top = failed_rules[0]
            if len(failed_rules) == 1:
                change = (
                    "a new scan inside the trial window"
                    if top["resolver"] == "scan_within_days"
                    else short_text(top["text"], 80)
                )
                blocking = f"Blocked by: {short_text(top['text'], 90)} — {top['reason']} What would change it: {change}."
            else:
                blocking = f"Excluded: {short_text(top['reason'], 140)}"
        ranked.append({
            **public,
            "met": met,
            "failed": failed,
            "unknown": unknown,
            "rules": rules_out,
            "base_failures": base_failures,
            "blocking": blocking,
            "provider": check.provider,
            "from_cache": check.from_cache,
            "hypothetical": any(rule["hypothetical"] for rule in rules_out),
        })
    ranked.sort(key=lambda row: (
        row["failed"], -row["met"], row["unknown"],
        row["nearest_distance_mi"] if row["nearest_distance_mi"] is not None else 10**9,
    ))
    for index, row in enumerate(ranked):
        row["rank"] = index + 1
        if row["failed"] == 0 and index == 0:
            row["tag"] = "best_fit"
        elif row["failed"] == 1:
            row["tag"] = "near_miss"
        elif row["failed"] >= 2:
            row["tag"] = "excluded"
        else:
            row["tag"] = None
    unchecked.sort(key=lambda row: row["nearest_distance_mi"] if row["nearest_distance_mi"] is not None else 10**9)
    question_input = [(row["nct_id"], session.checks[row["nct_id"]].rules) for row in ranked if row["nct_id"] in session.checks]
    # Questions are built from base rules so an answer does not remove the question.
    questions = questions_from(question_input, session.answers)
    what_ifs = what_ifs_from(ranked, session.what_ifs)
    for row in ranked:
        row.pop("base_failures", None)
    selected = next((row for row in ranked if row["nct_id"] == session.selected_nct), None)
    if selected is None and ranked:
        selected = ranked[0]
        session.selected_nct = selected["nct_id"]
    compare = next((row for row in ranked if row["nct_id"] == session.compare_nct), None)
    patient = session.patient.model_dump() if session.patient else None
    return {
        "patient": patient,
        "patient_source": session.patient_source,
        "filters": session.filters.model_dump(),
        "origin": {"lat": session.origin_lat, "lon": session.origin_lon, "label": session.origin_label},
        "ranked": ranked,
        "unchecked": unchecked,
        "unchecked_count": len(unchecked),
        "found_count": len(ranked) + len(unchecked),
        "checked_count": len(ranked),
        "questions": questions,
        "questions_open": sum(1 for item in questions if item["answer"] == "unknown"),
        "what_ifs": what_ifs,
        "selected": selected,
        "compare": compare,
        "answers": session.answers,
        "demo_cache": session.demo_cache,
        "llm_mode": session.llm_mode,
        "warning": session.warning,
        "error": session.error,
        "retrieved_at": session.retrieved_at,
        "source_recorded_at": session.source_recorded_at,
        "verification_explanation": SCORE_EXPLANATION,
        "source_label": session.source_label,
        "total_available": session.total_available,
        "fetched_radius_mi": session.fetched_radius_mi,
        "referral_note": session.referral_note,
        "summary_note": session.summary_note,
        "revision": session.revision,
        "updated_at": session.updated_at,
        "disclaimer": DISCLAIMER,
        "signature": signature(session.patient) if session.patient else None,
    }


def examples() -> list[dict[str, Any]]:
    items = []
    for key, patient in PREPARED.items():
        items.append({
            "id": key,
            "label": {
                "nsclc-austin": "NSCLC EGFR L858R · Austin",
                "breast-her2": "HER2+ breast · Houston",
                "crc-kras": "Colorectal KRAS G12C · Dallas",
                "blank": "Blank template",
            }[key],
            "patient": patient.model_dump(),
        })
    return items
