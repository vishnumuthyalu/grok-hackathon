import json
from datetime import date
from pathlib import Path

from trialradar.citations import cite_quote, provenance, verification
from trialradar.ctgov import parse_study
from trialradar.service import draft, load_example

from test_service import _cache

FIXTURE = json.loads(Path("tests/fixtures/nct04486833.json").read_text())


def test_quote_is_verified_only_when_verbatim_in_record():
    trial = parse_study(FIXTURE)
    line = next(item for item in trial.eligibility_criteria.splitlines() if len(item.strip()) > 30)
    quote = line.strip().lstrip("*-• ").strip()
    found = cite_quote(quote, trial)
    assert found["verified"] is True
    assert found["section"] == "Eligibility Criteria"
    assert found["url"].startswith(f"https://clinicaltrials.gov/study/{trial.nct_id}")
    fake = cite_quote("Patients must have a purple unicorn biomarker", trial)
    assert fake["verified"] is False
    assert fake["label"] == "Not found in source"


def test_verification_score_is_deterministic_and_honest():
    trial = parse_study(FIXTURE)
    trial.last_update = "2026-10-01"
    good = [{"verified": True}] * 4
    half = [{"verified": True}] * 2 + [{"verified": False}] * 2
    today = date(2026, 10, 20)
    assert verification(trial, good, today=today)["score"] == 100
    assert verification(trial, half, today=today)["score"] == 70
    stale = verification(trial, good, today=date(2028, 1, 1))
    assert stale["score"] == 90 and stale["fresh"] is False
    trial.nct_id = "NCT123"
    bad = verification(trial, good, today=today)
    assert bad["nct_id_valid"] is False and bad["score"] == 85
    assert "not" in bad["explanation"].lower() and "clinical-quality" in bad["explanation"]


def test_provenance_marks_demo_cache():
    trial = parse_study(FIXTURE)
    prov = provenance(trial, demo_cache=True, retrieved_at="x", recorded_at="2026-10-19T23:52:39+00:00")
    assert prov["api_url"] == f"https://clinicaltrials.gov/api/v2/studies/{trial.nct_id}"
    assert prov["retrieval_mode"] == "demo_cache"
    assert "recorded at 2026-10-19 23:52 UTC" in prov["retrieval_label"]


def test_view_and_notes_carry_citations(monkeypatch, tmp_path):
    trial = _cache(monkeypatch, tmp_path)
    payload = load_example("nsclc-austin")
    row = payload["ranked"][0]
    assert row["provenance"]["study_url"] == f"https://clinicaltrials.gov/study/{trial.nct_id}"
    assert row["provenance"]["retrieval_mode"] == "demo_cache"
    assert row["verification"]["quotes_total"] == len(row["rules"])
    assert row["verification"]["quotes_verified"] >= 1
    assert all("citation" in rule for rule in row["rules"])
    notes = draft(trial.nct_id)["notes"]
    for text in (notes["referral"], notes["summary"]):
        assert "Sources (ClinicalTrials.gov):" in text
        assert f"https://clinicaltrials.gov/study/{trial.nct_id}" in text
    assert notes["sources"][0].endswith(trial.nct_id)
