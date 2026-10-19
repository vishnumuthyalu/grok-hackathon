import json
from pathlib import Path

from trialradar.criteria import split_criteria
from trialradar.ctgov import parse_study
from trialradar.demo_patients import NSCLC_AUSTIN
from trialradar.eligibility import (
    apply_overrides,
    counts,
    evaluate_trial,
    judge_rule,
    rescore_rules,
)
from trialradar.extract import extract_patient_from_text
from trialradar.geo import geocode, haversine_miles
from trialradar.llm import StubProvider, set_provider
from trialradar.models import Patient
from trialradar.notes import draft_referral, draft_summary
from trialradar import DISCLAIMER

FIXTURE = json.loads(Path("tests/fixtures/nct04486833.json").read_text())


def test_parser_reads_live_api_shape():
    trial = parse_study(FIXTURE)
    assert trial.nct_id == "NCT04486833"
    assert trial.overall_status == "RECRUITING"
    assert "PHASE1" in trial.phases
    assert trial.url.endswith("NCT04486833")
    assert any(site.lat and site.lon for site in trial.sites)
    assert "Osimertinib" in trial.brief_title or "osimertinib" in trial.brief_title.lower()
    rules = split_criteria(trial.eligibility_criteria)
    kinds = {kind for _, kind, _ in rules}
    assert "inclusion" in kinds and "exclusion" in kinds
    assert len(rules) > 8


def test_age_ecog_egfr_and_prior_drug():
    patient = NSCLC_AUSTIN
    age = judge_rule("inclusion", "Age >= 18 years.", patient, "a")
    assert age.status == "met" and "62" in age.evidence
    ecog = judge_rule("inclusion", "ECOG performance status of 0 or 1.", patient, "e")
    assert ecog.status == "met"
    marker = judge_rule(
        "inclusion",
        "Documented EGFR mutation (exon 19 deletion or L858R).",
        patient,
        "m",
    )
    assert marker.status == "met"
    assert "L858R" in marker.evidence
    prior = judge_rule(
        "inclusion",
        "Progressive disease on osimertinib.",
        patient,
        "p",
    )
    assert prior.status == "met"
    naive = judge_rule(
        "inclusion",
        "No prior EGFR TKI therapy.",
        patient,
        "n",
    )
    assert naive.status == "not_met"
    assert naive.reversible is False


def test_one_chip_does_not_poison_another():
    breast = Patient(
        age=54, sex="F", cancer_type="Breast cancer", stage="IV", ecog=1,
        biomarkers=["HER2 positive", "ER negative", "PR negative"],
        prior_treatments=["Trastuzumab + pertuzumab + docetaxel"],
        city="Houston, TX",
    )
    her2 = judge_rule("inclusion", "HER2 overexpression (IHC 3+) or HER2 positive disease.", breast, "h")
    assert her2.status == "met"
    assert "HER2" in her2.evidence
    crc = Patient(
        age=58, sex="M", cancer_type="Colorectal cancer", stage="IV",
        biomarkers=["KRAS G12C", "MSS", "BRAF wild-type"],
        city="Dallas, TX",
    )
    kras = judge_rule("inclusion", "Documented KRAS G12C mutation.", crc, "k")
    assert kras.status == "met"
    assert "G12C" in kras.evidence


def test_egfr_none_fails_only_marker_rules():
    patient = NSCLC_AUSTIN.model_copy(deep=True)
    patient.biomarkers = ["EGFR none", "PD-L1 40%"]
    checked = evaluate_trial(patient, FIXTURE["protocolSection"]["eligibilityModule"]["eligibilityCriteria"])
    base = evaluate_trial(NSCLC_AUSTIN, FIXTURE["protocolSection"]["eligibilityModule"]["eligibilityCriteria"])
    changed = rescore_rules(base.rules, patient, {"biomarkers"})
    for before, after in zip(base.rules, changed):
        if "biomarkers" not in before.depends_on:
            assert after.status == before.status
            assert after.reason == before.reason
    assert any(rule.status == "not_met" and "EGFR" in rule.reason for rule in changed)
    assert counts(checked.rules)[1] >= 1


def test_scan_window_what_if_and_brain_answer_skip_the_model():
    patient = NSCLC_AUSTIN
    scan = judge_rule("inclusion", "Baseline CT or MRI scan within 28 days of enrollment.", patient, "s")
    assert scan.status == "not_met" and scan.reversible and scan.resolver == "scan_within_days"
    brain = judge_rule("inclusion", "No untreated brain metastases.", patient, "b")
    assert brain.status == "unknown" and brain.question_id == "brain_mets"
    flipped = apply_overrides([scan, brain], {"brain_mets": "no"}, {"scan_within_window": True})
    assert flipped[0].status == "met" and flipped[0].resolver == "scan_within_days_hypothetical"
    assert "Hypothetical" in flipped[0].reason
    assert flipped[1].status == "met"
    assert "Doctor answer" in flipped[1].evidence
    provider = StubProvider()
    set_provider(provider)
    provider.evaluate(patient, "Inclusion Criteria:\n* Age >= 18 years.")
    assert provider.calls == 1
    # Answers and what-ifs are overlays. They must not call the provider.
    apply_overrides(flipped, {"brain_mets": "yes"}, {})
    assert provider.calls == 1


def test_near_miss_is_exactly_one_failure():
    recent = NSCLC_AUSTIN.model_copy(update={"last_scan_days_ago": 10})
    checked = evaluate_trial(recent, """Inclusion Criteria:
* Age >= 18 years.
* Documented EGFR L858R mutation.
* Baseline CT or MRI scan within 28 days of enrollment.
* ECOG performance status 0 or 1.
""")
    met, failed, _unknown = counts(checked.rules)
    assert failed == 0
    assert met >= 3
    stale = recent.model_copy(update={"last_scan_days_ago": 47})
    checked_old = evaluate_trial(stale, """Inclusion Criteria:
* Age >= 18 years.
* Documented EGFR L858R mutation.
* Baseline CT or MRI scan within 28 days of enrollment.
""")
    assert counts(checked_old.rules)[1] == 1


def test_distance_and_hardcoded_cities_do_not_call_nominatim():
    austin = geocode("Austin, TX", allow_network=False)
    houston = geocode("Houston, TX", allow_network=False)
    assert austin == (30.2672, -97.7431)
    miles = haversine_miles(*austin, *houston)
    assert 140 <= miles <= 180
    assert geocode("Not A Real City, ZZ", allow_network=False) is None


def test_extract_synthetic_note_and_notes_are_not_sent():
    note = Path("demo/reports/nsclc_egfr_austin.txt").read_text()
    patient = extract_patient_from_text(note)
    assert patient.age == 62
    assert patient.sex == "F"
    assert "lung" in patient.cancer_type.lower()
    assert patient.stage == "IIIB"
    assert patient.city == "Austin, TX"
    assert patient.last_scan_days_ago == 47
    assert any("L858R" in item for item in patient.biomarkers)
    assert patient.prior_immunotherapy == "no"
    assert patient.brain_mets == "unknown"
    blank = extract_patient_from_text(Path("demo/reports/blank_template.txt").read_text())
    assert "age" in blank.missing_fields
    referral = draft_referral(
        patient.model_dump(),
        {"nct_id": "NCT04486833", "brief_title": "Example", "url": "https://clinicaltrials.gov/study/NCT04486833",
         "overall_status": "RECRUITING", "phases": ["PHASE2"], "nearest_label": "Austin, TX", "nearest_distance_mi": 6},
        [{"status": "met", "text": "Age >= 18", "reason": "Age 62", "boilerplate": False}],
    )
    summary = draft_summary(
        patient.model_dump(),
        {"nct_id": "NCT04486833", "brief_title": "Example", "nearest_label": "Austin, TX", "nearest_distance_mi": 6, "phases": []},
        [{"status": "unknown", "text": "Brain MRI", "reason": "missing", "boilerplate": False}],
    )
    for text in (referral, summary):
        assert DISCLAIMER in text
        assert "NOT SENT" in text or "does not contact" in text
        assert "email" not in text.lower()


def test_real_study_for_the_austin_patient_is_not_invented():
    trial = parse_study(FIXTURE)
    checked = evaluate_trial(NSCLC_AUSTIN, trial.eligibility_criteria)
    assert checked.rules
    assert all(rule.evidence for rule in checked.rules)
    met, failed, unknown = counts(checked.rules)
    assert met >= 5
    assert unknown > 0
    # The public record of this study requires an EGFR mutation. Clearing it fails that rule.
    cleared = NSCLC_AUSTIN.model_copy(deep=True)
    cleared.biomarkers = ["EGFR none"]
    rescored = rescore_rules(checked.rules, cleared, {"biomarkers"})
    assert any(rule.status == "not_met" for rule in rescored)
