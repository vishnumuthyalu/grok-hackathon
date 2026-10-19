"""Draft a referral note and a plain-language summary.

Both are built only from structured facts already on screen. Nothing is sent.
"""

from __future__ import annotations

from trialradar import DISCLAIMER


def _phase(phases: list[str]) -> str:
    numbers = []
    for phase in phases:
        for digit in ("1", "2", "3", "4"):
            if digit in phase and digit not in numbers:
                numbers.append(digit)
    if not numbers:
        return "Phase not listed"
    return "Phase " + "/".join(numbers)


def draft_referral(patient: dict, trial: dict, rules: list[dict]) -> str:
    met = sum(1 for rule in rules if rule["status"] == "met")
    failed = sum(1 for rule in rules if rule["status"] == "not_met")
    unknown = sum(1 for rule in rules if rule["status"] == "unknown")
    open_rules = [rule for rule in rules if rule["status"] == "unknown" and not rule.get("boilerplate")][:6]
    failed_rules = [rule for rule in rules if rule["status"] == "not_met"][:6]
    lines = [
        "DRAFT REFERRAL NOTE — NOT SENT",
        "TrialRadar does not contact patients or trial sites. Copy or download only.",
        "",
        f"Study: {trial.get('nct_id')} — {trial.get('brief_title')}",
        f"Link: {trial.get('url')}",
        f"Status: {trial.get('overall_status')} · {_phase(trial.get('phases') or [])}",
        f"Nearest listed site: {trial.get('nearest_label') or 'see ClinicalTrials.gov'} "
        f"({_miles(trial.get('nearest_distance_mi'))})",
        "",
        "Patient facts used (from the local record):",
        f"- Age {patient.get('age') or '—'}, sex {patient.get('sex') or '—'}, "
        f"{patient.get('cancer_type') or 'cancer not stated'}, stage {patient.get('stage') or '—'}",
        f"- ECOG {patient.get('ecog') if patient.get('ecog') is not None else '—'}",
        f"- Biomarkers: {', '.join(patient.get('biomarkers') or []) or '—'}",
        f"- Prior treatment: {'; '.join(patient.get('prior_treatments') or []) or '—'}",
        f"- City: {patient.get('city') or '—'}; last imaging: "
        f"{_scan(patient.get('last_scan_days_ago'))}",
        "",
        f"Eligibility snapshot on the rules we checked: {met} met, {failed} not met, {unknown} unknown.",
    ]
    if failed_rules:
        lines.append("")
        lines.append("Rules that did not match:")
        for rule in failed_rules:
            lines.append(f"- {rule['text'][:220]}")
            lines.append(f"  Reason: {rule['reason']}")
    if open_rules:
        lines.append("")
        lines.append("Still unknown — for the treating doctor:")
        for rule in open_rules:
            lines.append(f"- {rule['text'][:220]}")
    lines.extend(["", DISCLAIMER])
    return "\n".join(lines)


def draft_summary(patient: dict, trial: dict, rules: list[dict]) -> str:
    """Short sentences, everyday words, about an 8th-grade reading level."""
    failed = [rule for rule in rules if rule["status"] == "not_met"]
    unknown = [rule for rule in rules if rule["status"] == "unknown" and not rule.get("boilerplate")]
    cancer = patient.get("cancer_type") or "your cancer"
    city = patient.get("city") or "your city"
    title = trial.get("brief_title") or "a research study"
    distance = _miles(trial.get("nearest_distance_mi"))
    where = trial.get("nearest_label") or "a site listed on ClinicalTrials.gov"
    bits = [
        "This note is for you and your doctor. It is not an offer to join a study.",
        f"Your doctor is looking at research studies that are open near {city} for adults with {cancer}.",
        f"One study on the list is \"{title}\" ({trial.get('nct_id')}).",
        f"The closest listed site is in {where}, about {distance}.",
    ]
    markers = patient.get("biomarkers") or []
    if markers:
        bits.append("The match uses these facts from the record: " + "; ".join(markers) + ".")
    if not failed:
        bits.append("On the rules we could check, nothing in the record clearly rules this study out.")
    else:
        bits.append(
            f"{len(failed)} rule(s) do not match the record. Your doctor can see which ones. "
            "A rule that does not match means this study may not be an option."
        )
    if unknown:
        bits.append(
            "Some rules are still unknown because the record does not say. "
            "Your doctor may need another lab test or scan before deciding."
        )
    bits.append("Only your doctor can decide if a study is a good fit. This app does not contact you or the study site.")
    bits.append(DISCLAIMER)
    return " ".join(bits)


def _miles(value) -> str:
    if value is None:
        return "distance not listed"
    return f"{round(value)} miles away"


def _scan(days) -> str:
    if days is None:
        return "not in the record"
    return f"{days} days before this review"
