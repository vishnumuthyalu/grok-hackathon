"""Turn a note, OCR text, or a typed dict into a Patient. Never add facts that are not in the source."""

from __future__ import annotations

import re
from pathlib import Path

from trialradar.models import Patient

_LABELS = {
    "age": "age",
    "sex": "sex",
    "gender": "sex",
    "cancer type": "cancer_type",
    "diagnosis": "cancer_type",
    "cancer": "cancer_type",
    "histologic type": "histology",
    "stage": "stage",
    "ecog": "ecog",
    "ecog performance status": "ecog",
    "home city": "city",
    "city": "city",
    "location": "city",
    "last imaging": "last_scan",
    "last scan": "last_scan",
    "prior treatment": "prior",
    "prior treatments": "prior",
    "biomarkers": "markers",
    "prior immunotherapy": "prior_io",
    "brain mri": "brain",
    "notes": "notes",
}


def _blank() -> dict:
    return {
        "age": None,
        "sex": None,
        "cancer_type": None,
        "stage": None,
        "biomarkers": [],
        "prior_treatments": [],
        "ecog": None,
        "city": None,
        "last_scan_days_ago": None,
        "notes": None,
        "brain_mets": None,
        "kidney_adequate": None,
        "prior_immunotherapy": None,
        "measurable_disease": None,
        "line_of_therapy": None,
    }


def extract_patient_from_text(text: str) -> Patient:
    """Label-first extractor. Works on the synthetic reports and ordinary pasted notes."""
    fields = _blank()
    notes: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.upper().startswith("SYNTHETIC"):
            continue
        if ":" not in line:
            continue
        label, value = line.split(":", 1)
        key = _LABELS.get(label.strip().lower())
        value = value.strip()
        if not key or not value or value.lower() in {"—", "-", "n/a"}:
            continue
        if key == "age":
            match = re.search(r"\d{1,3}", value)
            if match:
                fields["age"] = int(match.group(0))
        elif key == "sex":
            fields["sex"] = value
        elif key == "cancer_type":
            fields["cancer_type"] = value
        elif key == "stage":
            match = re.search(r"\b(IV[AB]?|III[ABC]?|II[AB]?|I[AB]?)\b", value, re.I)
            fields["stage"] = match.group(1).upper() if match else value
        elif key == "ecog":
            match = re.search(r"[0-4]", value)
            if match:
                fields["ecog"] = int(match.group(0))
        elif key == "city":
            fields["city"] = value
        elif key == "last_scan":
            match = re.search(r"(\d+)\s*days?", value, re.I)
            if match:
                fields["last_scan_days_ago"] = int(match.group(1))
        elif key == "prior":
            fields["prior_treatments"] = [part.strip() for part in re.split(r";|\n", value) if part.strip()]
        elif key == "markers":
            fields["biomarkers"] = [part.strip() for part in value.split(";") if part.strip()]
        elif key == "prior_io":
            fields["prior_immunotherapy"] = _yes_no(value)
        elif key == "brain":
            fields["brain_mets"] = _brain(value)
        elif key == "notes":
            notes.append(value)
        elif key == "histology" and fields["cancer_type"] and value.lower() not in fields["cancer_type"].lower():
            notes.append(f"Histology: {value}")
    _from_prose(text, fields)
    if notes and not fields["notes"]:
        fields["notes"] = " ".join(notes)
    patient = Patient(**fields)
    patient.missing_fields = missing_fields(patient)
    return normalize_patient(patient)


def _yes_no(value: str) -> str | None:
    lowered = value.lower()
    if re.search(r"\b(no|none|not)\b", lowered):
        return "no"
    if re.search(r"\byes\b", lowered):
        return "yes"
    if "unknown" in lowered or "not in record" in lowered:
        return "unknown"
    return None


def _brain(value: str) -> str | None:
    lowered = value.lower()
    if "not in record" in lowered or "not available" in lowered or "not performed" in lowered or "missing" in lowered:
        return "unknown"
    if re.search(r"\b(no|negative|without|none)\b", lowered):
        return "no"
    if re.search(r"\b(yes|present|positive|identified)\b", lowered):
        return "yes"
    return "unknown"


def _from_prose(text: str, fields: dict) -> None:
    """Fill gaps from sentences when the note has no labels."""
    blob = " ".join(text.split())
    if fields["age"] is None:
        match = re.search(r"\b(\d{2})\s*(?:year[- ]old|yo|y/o|years old)\b", blob, re.I)
        if match:
            fields["age"] = int(match.group(1))
    if fields["sex"] is None:
        if re.search(r"\b(female|woman)\b", blob, re.I):
            fields["sex"] = "F"
        elif re.search(r"\b(male|man)\b", blob, re.I):
            fields["sex"] = "M"
    if fields["cancer_type"] is None:
        for pattern, label in (
            (r"non-small cell lung|nsclc", "Non-small cell lung cancer"),
            (r"breast", "Breast cancer"),
            (r"colorectal|colon cancer", "Colorectal cancer"),
        ):
            if re.search(pattern, blob, re.I):
                fields["cancer_type"] = label
                break
    if fields["stage"] is None:
        match = re.search(r"\bstage\s*(IV[AB]?|III[ABC]?|II[AB]?)\b", blob, re.I)
        if match:
            fields["stage"] = match.group(1).upper()
    if fields["ecog"] is None:
        match = re.search(r"ECOG(?:\s+performance status)?(?:\s+score)?\s*(?:of|:)?\s*([0-4])", blob, re.I)
        if match:
            fields["ecog"] = int(match.group(1))
    if fields["city"] is None:
        match = re.search(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)?),\s*([A-Z]{2})\b", text)
        if match:
            fields["city"] = f"{match.group(1)}, {match.group(2)}"
    if fields["last_scan_days_ago"] is None:
        match = re.search(r"(\d+)\s*days?\s*(?:ago|before|prior)", blob, re.I)
        if match:
            fields["last_scan_days_ago"] = int(match.group(1))
    if not fields["biomarkers"]:
        markers = []
        if re.search(r"L858R", blob):
            markers.append("EGFR L858R")
        elif re.search(r"exon\s*19\s*del", blob, re.I):
            markers.append("EGFR exon 19 deletion")
        if re.search(r"KRAS\s*G12C", blob, re.I):
            markers.append("KRAS G12C")
        if re.search(r"HER2[- ]?(positive|amplified|3\+)", blob, re.I):
            markers.append("HER2 positive")
        if re.search(r"triple[- ]negative|TNBC", blob, re.I):
            markers.append("TNBC")
        if re.search(r"\bMSI[- ]?H\b", blob):
            markers.append("MSI-H")
        if re.search(r"BRAF\s*V600E?", blob, re.I):
            markers.append("BRAF V600E")
        pdl = re.search(r"PD-?L1[^.]{0,20}?(\d{1,3})\s*%", blob, re.I)
        if pdl:
            markers.append(f"PD-L1 {pdl.group(1)}%")
        fields["biomarkers"] = markers
    if not fields["prior_treatments"]:
        found = []
        for name in ("osimertinib", "trastuzumab", "T-DXd", "FOLFOX", "FOLFIRI", "pembrolizumab"):
            if re.search(name, blob, re.I):
                found.append(name)
        fields["prior_treatments"] = found


def missing_fields(patient: Patient) -> list[str]:
    missing = []
    for field, label in (
        ("age", "age"),
        ("sex", "sex"),
        ("cancer_type", "cancer type"),
        ("stage", "stage"),
        ("ecog", "ECOG"),
        ("city", "city"),
        ("last_scan_days_ago", "last scan date"),
    ):
        if getattr(patient, field) in (None, ""):
            missing.append(label)
    if not patient.biomarkers:
        missing.append("biomarkers")
    if not patient.prior_treatments:
        missing.append("prior treatments")
    return missing


def normalize_patient(patient: Patient) -> Patient:
    if patient.sex:
        token = patient.sex.strip().lower()
        if token in {"f", "female"}:
            patient.sex = "F"
        elif token in {"m", "male"}:
            patient.sex = "M"
    if patient.line_of_therapy is None:
        items = [item for item in patient.prior_treatments if item.strip()]
        if items:
            patient.line_of_therapy = len(items)
    patient.biomarkers = [item.strip() for item in patient.biomarkers if item and item.strip()]
    patient.prior_treatments = [item.strip() for item in patient.prior_treatments if item and item.strip()]
    patient.missing_fields = missing_fields(patient)
    return patient


def ocr_path(path: Path) -> str:
    """Text layer first for PDFs. Images go through Tesseract."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        if len(text.strip()) >= 40:
            return text
    if suffix in {".txt", ".md", ".html", ".htm"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Install pillow and pytesseract to read report images.") from exc
    try:
        return pytesseract.image_to_string(Image.open(path))
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError(
            "Tesseract is not installed. On Debian/Ubuntu: sudo apt-get install tesseract-ocr. "
            "On macOS: brew install tesseract."
        ) from exc


def extract_from_path(path: Path) -> tuple[Patient, str]:
    text = ocr_path(path)
    if len(text.strip()) < 15:
        raise RuntimeError("No text could be read from that file.")
    return extract_patient_from_text(text), text
