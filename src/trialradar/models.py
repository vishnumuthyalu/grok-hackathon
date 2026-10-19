"""Shared patient, trial, and session models.

Eligibility status is about the patient passing the rule:
- inclusion + met: the requirement is satisfied
- exclusion + met: the exclusion does not apply
- not_met: the patient fails the rule
- unknown: the record does not contain enough evidence
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from trialradar import DISCLAIMER

Status = Literal["met", "not_met", "unknown"]
Answer = Literal["yes", "no", "unknown"]
Kind = Literal["inclusion", "exclusion"]


class Patient(BaseModel):
    id: str | None = None
    age: int | None = None
    sex: str | None = None
    cancer_type: str | None = None
    stage: str | None = None
    biomarkers: list[str] = Field(default_factory=list)
    prior_treatments: list[str] = Field(default_factory=list)
    ecog: int | None = None
    city: str | None = None
    last_scan_days_ago: int | None = None
    notes: str | None = None
    brain_mets: str | None = None
    kidney_adequate: str | None = None
    prior_immunotherapy: str | None = None
    measurable_disease: str | None = None
    line_of_therapy: int | None = None
    missing_fields: list[str] = Field(default_factory=list)

    def marker_text(self) -> str:
        return " ".join(self.biomarkers).lower()

    def prior_text(self) -> str:
        parts = list(self.prior_treatments)
        if self.notes:
            parts.append(self.notes)
        return " ".join(parts).lower()


class Filters(BaseModel):
    distance_mi: int = 100
    phases: list[str] = Field(default_factory=lambda: ["1", "2", "3"])
    recruiting_only: bool = True


class Site(BaseModel):
    facility: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    status: str | None = None
    lat: float | None = None
    lon: float | None = None
    distance_mi: float | None = None


class Trial(BaseModel):
    nct_id: str
    brief_title: str
    official_title: str | None = None
    overall_status: str
    phases: list[str] = Field(default_factory=list)
    study_type: str | None = None
    conditions: list[str] = Field(default_factory=list)
    interventions: list[str] = Field(default_factory=list)
    eligibility_criteria: str = ""
    minimum_age: str | None = None
    maximum_age: str | None = None
    sex: str | None = None
    std_ages: list[str] = Field(default_factory=list)
    sites: list[Site] = Field(default_factory=list)
    nearest_distance_mi: float | None = None
    nearest_label: str | None = None
    nearest_lat: float | None = None
    nearest_lon: float | None = None
    url: str
    last_update: str | None = None
    retrieval_score: int = 0


class Rule(BaseModel):
    id: str
    text: str
    kind: Kind
    status: Status
    reason: str
    evidence: str
    depends_on: list[str] = Field(default_factory=list)
    reversible: bool = False
    question_id: str | None = None
    question_text: str | None = None
    yes_status: Status | None = None
    no_status: Status | None = None
    resolver: str = "unknown"
    boilerplate: bool = False


class TrialCheck(BaseModel):
    nct_id: str
    rules: list[Rule] = Field(default_factory=list)
    provider: str = "stub"
    from_cache: bool = False


class Session(BaseModel):
    patient: Patient | None = None
    patient_source: str = "typed"
    filters: Filters = Field(default_factory=Filters)
    trials: list[Trial] = Field(default_factory=list)
    checks: dict[str, TrialCheck] = Field(default_factory=dict)
    answers: dict[str, Answer] = Field(default_factory=dict)
    what_ifs: dict[str, bool] = Field(default_factory=dict)
    selected_nct: str | None = None
    compare_nct: str | None = None
    origin_lat: float | None = None
    origin_lon: float | None = None
    origin_label: str | None = None
    fetched_radius_mi: int = 0
    demo_cache: bool = False
    llm_mode: str = "stub"
    warning: str | None = None
    error: str | None = None
    retrieved_at: str | None = None
    source_label: str = "ClinicalTrials.gov"
    total_available: int | None = None
    revision: int = 0
    updated_at: str = ""
    disclaimer: str = DISCLAIMER
