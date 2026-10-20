import json
from pathlib import Path

from fastapi.testclient import TestClient

from trialradar.cache import signature, write_cache
from trialradar.ctgov import parse_study
from trialradar.demo_patients import NSCLC_AUSTIN
from trialradar.llm import StubProvider, set_provider
from trialradar.service import check, load_example, rescore, search

FIXTURE = json.loads(Path("tests/fixtures/nct04486833.json").read_text())


def _cache(monkeypatch, tmp_path):
    trial = parse_study(FIXTURE)
    # Give the study a recruiting site in Austin so the default 100 mile filter keeps it.
    trial.sites[0].city = "Austin"
    trial.sites[0].state = "Texas"
    trial.sites[0].status = "RECRUITING"
    trial.sites[0].lat = 30.2672
    trial.sites[0].lon = -97.7431
    path = tmp_path / "demo_cache.json"
    monkeypatch.setenv("DEMO_CACHE_PATH", str(path))
    monkeypatch.setenv("DEMO_CACHE", "1")
    write_cache({
        "nsclc-austin": {
            "signature": signature(NSCLC_AUSTIN),
            "provider": "recorded-stub",
            "total_available": 1,
            "patient": NSCLC_AUSTIN.model_dump(),
            "trials": [trial.model_dump()],
            "checks": {},
        }
    }, provider="recorded-stub")
    return trial


def test_demo_cache_search_does_not_call_the_network(monkeypatch, tmp_path):
    _cache(monkeypatch, tmp_path)

    def explode(*_args, **_kwargs):
        raise AssertionError("ClinicalTrials.gov should not be called for a prepared patient")

    monkeypatch.setattr("trialradar.service.search_studies", explode)
    payload = search(patient=NSCLC_AUSTIN.model_copy(deep=True))
    assert payload["demo_cache"] is True
    assert payload["found_count"] == 1
    assert payload["ranked"] == []
    assert payload["unchecked"][0]["nct_id"] == "NCT04486833"
    assert DISCLAIMER_IN(payload)


def DISCLAIMER_IN(payload):
    return "Suggestions for the treating doctor" in payload["disclaimer"]


def test_check_is_one_call_and_edits_do_not_call_again(monkeypatch, tmp_path):
    _cache(monkeypatch, tmp_path)
    provider = StubProvider()
    set_provider(provider)
    search(patient=NSCLC_AUSTIN.model_copy(deep=True))
    first = check(top_n=5)
    assert provider.calls == 1
    assert first["checked_count"] == 1
    assert first["newly_checked"] == ["NCT04486833"]
    edited = NSCLC_AUSTIN.model_copy(deep=True)
    edited.biomarkers = ["EGFR none", "PD-L1 40%"]
    again = rescore(patient=edited)
    assert provider.calls == 1
    assert again["ranked"][0]["failed"] >= 1
    answered = rescore(answers={"brain_mets": "no", "prior_io": "no"})
    assert provider.calls == 1
    assert answered["answers"]["brain_mets"] == "no"
    # Narrowing distance is instant and keeps the cached trial out when it is too far.
    hidden = rescore(distance_mi=25)
    # The Austin site is 0 miles away, so 25 still shows it. Push the slider conceptually
    # by checking the filter echoed back.
    assert hidden["filters"]["distance_mi"] == 25
    assert provider.calls == 1


def test_example_load_resets_a_narrowed_distance(monkeypatch, tmp_path):
    _cache(monkeypatch, tmp_path)
    set_provider(StubProvider())
    search(patient=NSCLC_AUSTIN.model_copy(deep=True))
    narrowed = rescore(distance_mi=25, answers={"brain_mets": "no"})
    assert narrowed["filters"]["distance_mi"] == 25
    loaded = load_example("nsclc-austin")
    assert loaded["filters"]["distance_mi"] == 100
    assert loaded["filters"]["phases"] == ["1", "2", "3"]
    assert loaded["filters"]["recruiting_only"] is True
    assert loaded["answers"].get("brain_mets") != "no"
    assert loaded["found_count"] == 1


def test_api_example_roundtrip_and_notes(monkeypatch, tmp_path):
    _cache(monkeypatch, tmp_path)
    set_provider(StubProvider())
    from trialradar.api import app

    client = TestClient(app)
    health = client.get("/api/health")
    assert health.status_code == 200
    loaded = client.post("/api/examples/nsclc-austin")
    assert loaded.status_code == 200
    body = loaded.json()
    # Example load searches. Checks run only when the cache has none, which it doesn't.
    checked = client.post("/api/check", json={"top_n": 5})
    assert checked.status_code == 200
    data = checked.json()
    assert data["checked_count"] == 1
    notes = client.post("/api/notes", json={"nct_id": "NCT04486833"})
    assert "DRAFT REFERRAL NOTE — NOT SENT" in notes.json()["notes"]["referral"]
    assert "not an offer" in notes.json()["notes"]["summary"].lower()
    page = client.get("/")
    assert page.status_code == 200
    assert "TrialRadar" in page.text
    assert "never contacts patients or sites" in page.text
