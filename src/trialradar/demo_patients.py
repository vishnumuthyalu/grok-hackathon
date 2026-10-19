"""Three synthetic adult patients used by the demo cache and the example loader."""

from __future__ import annotations

from trialradar.models import Patient

NSCLC_AUSTIN = Patient(
    id="nsclc-austin",
    age=62,
    sex="F",
    cancer_type="Non-small cell lung cancer",
    stage="IIIB",
    biomarkers=["EGFR L858R", "PD-L1 40%"],
    prior_treatments=["Osimertinib (progressed)"],
    ecog=1,
    city="Austin, TX",
    last_scan_days_ago=47,
    notes="Progressed on first-line osimertinib. Brain MRI is not in the record. No creatinine clearance in the note.",
    brain_mets="unknown",
    prior_immunotherapy="no",
    line_of_therapy=1,
)

BREAST_HER2 = Patient(
    id="breast-her2",
    age=54,
    sex="F",
    cancer_type="Breast cancer",
    stage="IV",
    biomarkers=["HER2 positive", "ER negative", "PR negative"],
    prior_treatments=["Trastuzumab + pertuzumab + docetaxel", "T-DXd (progressed)"],
    ecog=1,
    city="Houston, TX",
    last_scan_days_ago=21,
    notes="Metastatic HER2-positive breast cancer after two lines. Brain MRI negative.",
    brain_mets="no",
    prior_immunotherapy="no",
    line_of_therapy=2,
)

CRC_KRAS = Patient(
    id="crc-kras",
    age=58,
    sex="M",
    cancer_type="Colorectal cancer",
    stage="IV",
    biomarkers=["KRAS G12C", "MSS", "BRAF wild-type"],
    prior_treatments=["FOLFOX + bevacizumab", "FOLFIRI (progressed)"],
    ecog=0,
    city="Dallas, TX",
    last_scan_days_ago=18,
    notes="Metastatic colorectal adenocarcinoma with KRAS G12C. Microsatellite stable.",
    brain_mets="unknown",
    prior_immunotherapy="no",
    line_of_therapy=2,
)

BLANK = Patient(id="blank")

PREPARED = {
    "nsclc-austin": NSCLC_AUSTIN,
    "breast-her2": BREAST_HER2,
    "crc-kras": CRC_KRAS,
    "blank": BLANK,
}
