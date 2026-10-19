"""MCP server for an AI assistant that has never seen TrialRadar.

Run stdio (what Goose and Cursor launch):
    python -m trialradar.mcp_server

Optional streamable HTTP:
    python -m trialradar.mcp_server --http
"""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

from trialradar import DISCLAIMER
from trialradar.models import Patient
from trialradar.service import check as run_check
from trialradar.service import draft as run_draft
from trialradar.service import extract as run_extract
from trialradar.service import rescore as run_rescore
from trialradar.service import search as run_search
from trialradar.session import load, save

mcp = FastMCP(
    "TrialRadar",
    instructions=(
        "TrialRadar finds recruiting adult cancer trials and explains every eligibility "
        "rule as met, not met, or unknown. Work in this order: extract_patient, "
        "find_trials, check_eligibility, rescore if the doctor answers or edits a fact, "
        "then draft_notes. Never invent a trial, a biomarker, or a lab. "
        "Do not contact patients or trial sites. Every result is a suggestion for the "
        "treating doctor to confirm. Copy or download the drafts only."
    ),
)


def _patient(data: dict | None) -> Patient | None:
    if not data:
        return None
    return Patient.model_validate(data)


@mcp.tool()
def extract_patient(
    note_text: str | None = None,
    report_path: str | None = None,
    patient: dict | None = None,
) -> dict:
    """Read the patient and store them for the doctor's TrialRadar window.

    Call this first. Pass ONE of:
    - note_text: a pasted oncology note or the synthetic demo report
    - report_path: a local PDF or image path (text PDF, or Tesseract OCR)
    - patient: fields the doctor already knows (age, sex, cancer_type, stage,
      biomarkers, prior_treatments, ecog, city, last_scan_days_ago, notes)

    Do not fill in facts that are not in the source. Missing facts come back
    in missing_fields and later become doctor questions.

    Returns the patient JSON, missing_fields, and next_step.
    The doctor's browser, if it is open, shows this same patient.
    """
    payload = run_extract(note_text=note_text, report_path=report_path, patient=patient, source="mcp")
    payload["next_step"] = "Call find_trials. You can omit the patient; the one just stored is used."
    payload["disclaimer"] = DISCLAIMER
    return _compact_patient(payload)


@mcp.tool()
def find_trials(
    patient: dict | None = None,
    distance_mi: int = 100,
    phases: list[str] | None = None,
    recruiting_only: bool = True,
) -> dict:
    """Retrieve real recruiting interventional trials near the patient from ClinicalTrials.gov.

    Uses the public API (no key). Pass patient only to replace the stored one.
    distance_mi is the travel radius (default 100). phases is a list such as
    ["1", "2", "3"]. recruiting_only should stay true unless the doctor asks
    to see trials that are not yet recruiting.

    Does not judge eligibility. Call check_eligibility next.
    With DEMO_CACHE=1, the three prepared patients return recorded
    ClinicalTrials.gov studies so the demo works offline. Other patients are
    looked up live. Trial ids, titles, and criteria are never invented.
    """
    session = load()
    session.filters.distance_mi = distance_mi
    if phases is not None:
        session.filters.phases = [str(item) for item in phases]
    session.filters.recruiting_only = recruiting_only
    save(session)
    payload = run_search(patient=_patient(patient))
    payload["next_step"] = (
        "Call check_eligibility to score the top 5 trials. "
        "One model call covers every rule of a trial."
    )
    payload["disclaimer"] = DISCLAIMER
    return _compact_trials(payload)


@mcp.tool()
def check_eligibility(
    nct_ids: list[str] | None = None,
    top_n: int = 5,
    next_batch: bool = False,
) -> dict:
    """Score eligibility for the top trials, every rule in one pass per trial.

    Each rule is met, not_met, or unknown, with a reason and an evidence
    snippet from the patient record (or "no evidence"). Unknown rules become
    yes/no questions for the doctor.

    Leave nct_ids empty to score the first top_n visible trials that are not
    scored yet. If a demo-cache recording already scored them, this returns
    those recorded judgments and does not call the model. Set next_batch true
    to score the following batch (the "check next 5" button).

    Near miss means exactly one failed rule. The blocking text says what
    would change it. Read ranked[].rules before telling the doctor anything.
    """
    payload = run_check(nct_ids=nct_ids, top_n=top_n, next_batch=next_batch)
    payload["next_step"] = (
        "If questions are open, ask the doctor and call rescore with answers. "
        "A what-if switch is hypothetical. Then call draft_notes. "
        "Do not contact a site."
    )
    payload["disclaimer"] = DISCLAIMER
    return _compact_checks(payload)


@mcp.tool()
def rescore(
    patient: dict | None = None,
    answers: dict | None = None,
    what_ifs: dict | None = None,
    distance_mi: int | None = None,
    phases: list[str] | None = None,
    recruiting_only: bool | None = None,
) -> dict:
    """Re-rank the trials already on screen. Does not start a new trial search.

    Use this when the doctor edits a fact, answers a question, flips a what-if,
    or moves the distance / phase / recruiting filters.

    answers maps question id to "yes", "no", or "unknown"
    (for example {"brain_mets": "no", "prior_io": "no"}).
    what_ifs maps a switch id to true or false
    (for example {"scan_within_window": true}). What-ifs are hypothetical
    and must be described that way to the doctor.

    Only rules that depend on an edited patient fact are re-checked. Answers,
    what-ifs, and filter changes do not call the language model. The doctor's
    window updates from the same session.
    """
    payload = run_rescore(
        patient=_patient(patient),
        answers=answers,
        what_ifs=what_ifs,
        distance_mi=distance_mi,
        phases=phases,
        recruiting_only=recruiting_only,
    )
    payload["next_step"] = "Call draft_notes when the doctor wants a referral draft or a plain-language summary."
    payload["disclaimer"] = DISCLAIMER
    return _compact_checks(payload)


@mcp.tool()
def draft_notes(nct_id: str | None = None) -> dict:
    """Write two drafts for the selected trial. Nothing is sent.

    Returns:
    - referral: a note the doctor could edit before deciding whether to refer
    - summary: plain language, about an 8th-grade reading level, for the patient
    - delivery: copy_or_download_only

    Pass nct_id or omit it to use the trial selected in the window.
    Both drafts include the disclaimer. Do not email, fax, or submit them.
    """
    payload = run_draft(nct_id=nct_id)
    notes = payload.get("notes") or {}
    return {
        "nct_id": notes.get("nct_id"),
        "referral": notes.get("referral"),
        "summary": notes.get("summary"),
        "delivery": "copy_or_download_only",
        "disclaimer": DISCLAIMER,
        "error": payload.get("error"),
        "next_step": "Show both drafts to the doctor. Stop. Do not contact the patient or the site.",
    }


def _compact_patient(payload: dict) -> dict:
    return {
        "patient": payload.get("extracted_patient") or payload.get("patient"),
        "missing_fields": payload.get("missing_fields") or [],
        "warning": payload.get("warning"),
        "error": payload.get("error"),
        "next_step": payload.get("next_step"),
        "disclaimer": DISCLAIMER,
    }


def _compact_trials(payload: dict) -> dict:
    trials = []
    for row in (payload.get("ranked") or []) + (payload.get("unchecked") or []):
        trials.append({
            "nct_id": row.get("nct_id"),
            "brief_title": row.get("brief_title"),
            "phase": row.get("phase_label"),
            "status": row.get("overall_status"),
            "distance_mi": row.get("nearest_distance_mi"),
            "where": row.get("nearest_label"),
            "url": row.get("url"),
            "checked": "met" in row,
        })
    return {
        "found": payload.get("found_count"),
        "checked": payload.get("checked_count"),
        "source": payload.get("source_label"),
        "demo_cache": payload.get("demo_cache"),
        "trials": trials[:20],
        "warning": payload.get("warning"),
        "error": payload.get("error"),
        "next_step": payload.get("next_step"),
        "disclaimer": DISCLAIMER,
    }


def _compact_checks(payload: dict) -> dict:
    ranked = []
    for row in payload.get("ranked") or []:
        ranked.append({
            "rank": row.get("rank"),
            "nct_id": row.get("nct_id"),
            "brief_title": row.get("brief_title"),
            "phase": row.get("phase_label"),
            "distance_mi": row.get("nearest_distance_mi"),
            "where": row.get("nearest_label"),
            "url": row.get("url"),
            "met": row.get("met"),
            "failed": row.get("failed"),
            "unknown": row.get("unknown"),
            "tag": row.get("tag"),
            "blocking": row.get("blocking"),
            "rules": [
                {
                    "text": rule.get("text"),
                    "kind": rule.get("kind"),
                    "status": rule.get("status"),
                    "reason": rule.get("reason"),
                    "evidence": rule.get("evidence"),
                    "hypothetical": rule.get("hypothetical"),
                }
                for rule in row.get("rules") or []
                if not (rule.get("boilerplate") and rule.get("status") == "unknown")
            ],
        })
    return {
        "ranked": ranked,
        "questions": payload.get("questions"),
        "what_ifs": payload.get("what_ifs"),
        "newly_checked": payload.get("newly_checked"),
        "llm_mode": payload.get("llm_mode"),
        "demo_cache": payload.get("demo_cache"),
        "warning": payload.get("warning"),
        "error": payload.get("error"),
        "next_step": payload.get("next_step"),
        "disclaimer": DISCLAIMER,
    }


def main() -> None:
    transport = "stdio"
    if "--http" in sys.argv:
        transport = "streamable-http"
        mcp.settings.host = os.environ.get("MCP_HOST", "127.0.0.1")
        mcp.settings.port = int(os.environ.get("MCP_PORT", "8001"))
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
