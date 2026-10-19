"""Record ClinicalTrials.gov studies and rule judgments for the three demo patients."""

from __future__ import annotations

import os

from trialradar.cache import signature, write_cache
from trialradar.demo_patients import BREAST_HER2, CRC_KRAS, NSCLC_AUSTIN
from trialradar.service import check, search
from trialradar.session import load


def main() -> None:
    os.environ["DEMO_CACHE"] = "0"
    provider = os.environ.get("LLM_PROVIDER", "stub")
    patients = {
        "nsclc-austin": NSCLC_AUSTIN,
        "breast-her2": BREAST_HER2,
        "crc-kras": CRC_KRAS,
    }
    recorded = {}
    for key, patient in patients.items():
        print(f"Recording {key} from ClinicalTrials.gov ({provider})...")
        search(patient=patient.model_copy(deep=True), force_live=True)
        check(top_n=int(os.environ.get("CHECK_TOP_N", "5")))
        session = load()
        if session.error:
            raise SystemExit(f"{key}: {session.error}")
        if not session.trials:
            raise SystemExit(f"{key}: ClinicalTrials.gov returned no studies")
        recorded[key] = {
            "signature": signature(patient),
            "provider": session.llm_mode,
            "total_available": session.total_available,
            "fetched_radius_mi": session.fetched_radius_mi,
            "patient": patient.model_dump(),
            "trials": [trial.model_dump() for trial in session.trials],
            "checks": {nct: item.model_dump() for nct, item in session.checks.items()},
        }
        print(f"  {len(session.trials)} studies, {len(session.checks)} checked")
    write_cache(recorded, provider=provider)
    print("Wrote demo cache.")


if __name__ == "__main__":
    main()
