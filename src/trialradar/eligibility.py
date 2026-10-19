"""Judge one trial's rules and rank the results.

The local rules engine is both the stub LLM provider and the rescore path.
It only marks met or not_met when the patient record contains the fact the
rule asks about. Otherwise the rule stays unknown.
"""

from __future__ import annotations

import re
from typing import Callable

from trialradar.criteria import split_criteria
from trialradar.models import Patient, Rule, TrialCheck

_NORM = str.maketrans({"≥": ">=", "≤": "<=", "–": "-", "—": "-", "−": "-", "µ": "u", "“": '"', "”": '"'})

Matcher = Callable[[str, str, Patient], Rule | None]


def norm(text: str) -> str:
    return text.translate(_NORM)


def _rule(
    kind: str,
    text: str,
    *,
    status: str,
    reason: str,
    evidence: str,
    depends_on: list[str],
    reversible: bool = False,
    question_id: str | None = None,
    question_text: str | None = None,
    yes_status: str | None = None,
    no_status: str | None = None,
    resolver: str,
    boilerplate: bool = False,
    rule_id: str = "",
) -> Rule:
    return Rule(
        id=rule_id,
        text=text,
        kind=kind,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        reason=reason,
        evidence=evidence,
        depends_on=depends_on,
        reversible=reversible,
        question_id=question_id,
        question_text=question_text,
        yes_status=yes_status,  # type: ignore[arg-type]
        no_status=no_status,  # type: ignore[arg-type]
        resolver=resolver,
        boilerplate=boilerplate,
    )


def _family(cancer: str | None) -> str | None:
    text = (cancer or "").lower()
    if any(token in text for token in ("lung", "nsclc")):
        return "lung"
    if "breast" in text:
        return "breast"
    if any(token in text for token in ("colon", "colorectal", "rectal")):
        return "crc"
    return None


def _pdl1(patient: Patient) -> int | None:
    match = re.search(r"pd-?l1[^0-9]{0,12}(\d{1,3})\s*%", patient.marker_text())
    if not match:
        match = re.search(r"(\d{1,3})\s*%", patient.marker_text())
        if match and "pd" not in patient.marker_text():
            return None
    if not match:
        return None
    value = int(match.group(1))
    return value if value <= 100 else None


def _marker_is_negative(patient: Patient, gene: str) -> bool:
    """True only when a biomarker chip for this gene itself says negative.

    Chips are judged one at a time so 'HER2 positive' is not read as negative
    just because a later chip says 'ER negative' or 'BRAF wild-type'.
    """
    names = ["her2", "erbb2"] if gene == "her2" else [gene]
    for chip in patient.biomarkers:
        text = chip.lower().replace("her-2", "her2")
        if not any(name in text for name in names):
            continue
        negative = bool(re.search(r"\b(negative|none|not detected|wild[- ]type|absent)\b", text))
        positive = bool(re.search(r"\b(positive|amplified|mutant|mutation|detected|\+)\b", text))
        if negative and not positive:
            return True
    return False


def _has_term(blob: str, terms: list[str]) -> str | None:
    for term in terms:
        if term in blob:
            return term
    return None


def _quote_marker(patient: Patient, term: str) -> str:
    for chip in patient.biomarkers:
        if term in chip.lower():
            return chip
    return term


def _quote_prior(patient: Patient, term: str) -> str:
    for item in patient.prior_treatments:
        if term in item.lower():
            return item
    if patient.notes and term in patient.notes.lower():
        return patient.notes[:180]
    return term


# --- matchers: return a Rule with id "" when the rule is about this topic ---


def _match_age(text: str, kind: str, patient: Patient) -> Rule | None:
    lowered = text.lower()
    if not re.search(r"\bage\b|years?\b", lowered):
        return None
    if not re.search(r"\d", lowered):
        return None
    # Avoid lab values such as "28 days" being read as ages. Age rules say years or age.
    minimum = None
    maximum = None
    min_match = re.search(r"(?:>=|at least|older than|over)\s*(\d{1,3})\s*(?:years|yrs|year)?", lowered)
    if min_match and ("year" in lowered or "age" in lowered):
        minimum = int(min_match.group(1))
    max_match = re.search(r"(?:<=|younger than|under|no more than)\s*(\d{1,3})\s*(?:years|yrs|year)?", lowered)
    if max_match:
        maximum = int(max_match.group(1))
    between = re.search(r"(\d{1,3})\s*(?:years)?\s*(?:to|-)\s*(\d{1,3})\s*years", lowered)
    if between:
        minimum = int(between.group(1))
        maximum = int(between.group(2))
    if minimum is None and maximum is None:
        return None
    if minimum is not None and minimum > 100:
        return None
    if patient.age is None:
        return _rule(
            kind, text, status="unknown", reason="Age is not in the record.",
            evidence="no evidence", depends_on=["age"], resolver="age",
        )
    if minimum is not None and patient.age < minimum:
        return _rule(
            kind, text, status="not_met",
            reason=f"Recorded age {patient.age} is below {minimum}.",
            evidence=f"Age {patient.age}", depends_on=["age"], resolver="age",
        )
    if maximum is not None and patient.age > maximum:
        return _rule(
            kind, text, status="not_met",
            reason=f"Recorded age {patient.age} is above {maximum}.",
            evidence=f"Age {patient.age}", depends_on=["age"], resolver="age",
        )
    detail = []
    if minimum is not None:
        detail.append(f">= {minimum}")
    if maximum is not None:
        detail.append(f"<= {maximum}")
    return _rule(
        kind, text, status="met",
        reason=f"Recorded age {patient.age} meets {' and '.join(detail)}.",
        evidence=f"Age {patient.age}", depends_on=["age"], resolver="age",
    )


def _match_ecog(text: str, kind: str, patient: Patient) -> Rule | None:
    lowered = text.lower()
    if "ecog" not in lowered and "performance status" not in lowered:
        return None
    upper = None
    pair = re.search(r"(?:from\s*)?([0-4])\s*(?:or|to|-)\s*([0-4])", lowered)
    if pair:
        upper = max(int(pair.group(1)), int(pair.group(2)))
    else:
        single = re.search(r"(?:<=|of|score of)\s*([0-4])", lowered)
        if single:
            upper = int(single.group(1))
    if upper is None:
        return None
    if patient.ecog is None:
        return _rule(
            kind, text, status="unknown",
            reason="ECOG performance status is not in the record.",
            evidence="no evidence", depends_on=["ecog"], reversible=True,
            question_id="ecog", question_text="ECOG performance status 0–1?",
            yes_status="met" if upper >= 1 else "unknown",
            no_status="not_met", resolver="ecog",
        )
    if patient.ecog <= upper:
        return _rule(
            kind, text, status="met",
            reason=f"ECOG {patient.ecog} is within 0–{upper}.",
            evidence=f"ECOG {patient.ecog}", depends_on=["ecog"], resolver="ecog",
        )
    return _rule(
        kind, text, status="not_met", reversible=True,
        reason=f"ECOG {patient.ecog} is above the stated maximum of {upper}.",
        evidence=f"ECOG {patient.ecog}", depends_on=["ecog"], resolver="ecog",
    )


def _match_sex(text: str, kind: str, patient: Patient) -> Rule | None:
    lowered = text.lower()
    if not re.search(r"\b(male|female|women|men)\b", lowered):
        return None
    if re.search(r"breastfeed|breast-feed|donate ova|ova starting", lowered) and (patient.sex or "").upper() == "M":
        return _rule(
            kind, text, status="met",
            reason="This requirement applies to female patients. Recorded sex is M.",
            evidence="Sex M", depends_on=["sex"], resolver="sex",
        )
    if re.search(r"childbearing|post-?menopausal|pregnan|sperm|breastfeed", lowered):
        if re.search(r"\bif male\b|sperm", lowered) and (patient.sex or "").upper() == "F":
            return _rule(
                kind, text, status="met",
                reason="This requirement applies to male patients. Recorded sex is F.",
                evidence="Sex F", depends_on=["sex"], resolver="sex",
            )
        if re.search(r"\bif female\b|female of childbearing|focbp", lowered) and (patient.sex or "").upper() == "M":
            return _rule(
                kind, text, status="met",
                reason="This requirement applies to female patients. Recorded sex is M.",
                evidence="Sex M", depends_on=["sex"], resolver="sex",
            )
        return None
    if re.search(r"male (or|and) female|female (or|and) male|men and women|women and men", lowered):
        return _rule(
            kind, text, status="met", reason="The study enrolls more than one sex.",
            evidence=f"Sex {patient.sex}" if patient.sex else "no evidence",
            depends_on=["sex"], resolver="sex",
        )
    wants_female = bool(re.search(r"\b(female|women|woman)\b", lowered)) and not re.search(r"\bmale\b", lowered)
    wants_male = bool(re.search(r"\b(male|men|man)\b", lowered)) and not re.search(r"\b(female|women)\b", lowered)
    if not wants_female and not wants_male:
        return None
    if not patient.sex:
        return _rule(
            kind, text, status="unknown", reason="Sex is not in the record.",
            evidence="no evidence", depends_on=["sex"], resolver="sex",
        )
    sex = patient.sex.upper()
    if wants_female and sex == "F":
        return _rule(
            kind, text, status="met", reason="Recorded sex is F.",
            evidence="Sex F", depends_on=["sex"], resolver="sex",
        )
    if wants_male and sex == "M":
        return _rule(
            kind, text, status="met", reason="Recorded sex is M.",
            evidence="Sex M", depends_on=["sex"], resolver="sex",
        )
    if wants_female and sex == "M":
        return _rule(
            kind, text, status="not_met", reason="This rule is limited to female patients. Recorded sex is M.",
            evidence="Sex M", depends_on=["sex"], resolver="sex",
        )
    if wants_male and sex == "F":
        return _rule(
            kind, text, status="not_met", reason="This rule is limited to male patients. Recorded sex is F.",
            evidence="Sex F", depends_on=["sex"], resolver="sex",
        )
    return _rule(
        kind, text, status="unknown", reason="Sex in the record does not map cleanly onto this rule.",
        evidence=f"Sex {patient.sex}", depends_on=["sex"], resolver="sex",
    )


def _scan_days(lowered: str) -> int | None:
    if any(bad in lowered for bad in (
        "surgery", "washout", "adverse event", "last dose", "contraception",
        "prior therapy", "prior treatment", "lvef", "ejection", "bone marrow",
        "hemoglobin", "platelet", "neutrophil", "organ function", "blood count",
        "herbal", "investigational", "anti-tumor", "antineoplastic",
    )):
        return None
    if not re.search(
        r"\b(imaging|scans?|radiograph\w*|radiologic\w*|tumor assessment|computed tomography|magnetic resonance|mri|recist)\b",
        lowered,
    ):
        return None
    match = re.search(r"within\s*(\d+)\s*(day|week)", lowered)
    if not match:
        return None
    count = int(match.group(1))
    if match.group(2).startswith("week"):
        count *= 7
    return count


def _match_scan(text: str, kind: str, patient: Patient) -> Rule | None:
    days = _scan_days(text.lower())
    if days is None:
        return None
    if patient.last_scan_days_ago is None:
        return _rule(
            kind, text, status="unknown", reversible=True,
            reason=f"The rule asks for imaging within {days} days. No imaging date is in the record.",
            evidence="no evidence", depends_on=["last_scan_days_ago"], resolver="scan_within_days",
        )
    if patient.last_scan_days_ago <= days:
        return _rule(
            kind, text, status="met", reversible=True,
            reason=f"Last imaging was {patient.last_scan_days_ago} days ago, inside {days} days.",
            evidence=f"Last scan {patient.last_scan_days_ago} days ago",
            depends_on=["last_scan_days_ago"], resolver="scan_within_days",
        )
    return _rule(
        kind, text, status="not_met", reversible=True,
        reason=f"Last imaging was {patient.last_scan_days_ago} days ago. The rule asks for imaging within {days} days.",
        evidence=f"Last scan {patient.last_scan_days_ago} days ago",
        depends_on=["last_scan_days_ago"], resolver="scan_within_days",
    )


def _match_brain(text: str, kind: str, patient: Patient) -> Rule | None:
    lowered = text.lower()
    if not re.search(r"brain metast|cns metast|intracranial|leptomeningeal", lowered):
        return None
    # Stable/treated disease is allowed: a "yes" answer is not automatically a failure.
    allows_stable = bool(re.search(r"stable|treated|asymptomatic|previously treated", lowered))
    question = "Brain metastases on recent MRI?"
    if allows_stable:
        yes_status, no_status = "unknown", "met"
        base_reason = "The study may allow treated brain metastases. The record does not say whether any are present."
    else:
        yes_status, no_status = "not_met", "met"
        base_reason = "No brain MRI result is in the record."
    return _rule(
        kind, text, status="unknown", reason=base_reason, evidence="no evidence",
        depends_on=["brain_mets"], question_id="brain_mets", question_text=question,
        yes_status=yes_status, no_status=no_status, resolver="question",
    )


def _match_kidney(text: str, kind: str, patient: Patient) -> Rule | None:
    lowered = text.lower()
    # "gfr" is a substring of "egfr". Only treat EGFR as kidney function with a renal cue.
    renal = bool(re.search(r"creatinine|\bcrcl\b|cockcroft|glomerular|renal function|kidney function|\bgfr\b", lowered))
    egfr_kidney = "egfr" in lowered and bool(re.search(r"ml/min|glomerular|creatinine|renal|cockcroft", lowered))
    if not renal and not egfr_kidney:
        return None
    threshold = 50
    match = re.search(r"(?:>=|>)\s*(\d{2,3})", lowered)
    if match:
        threshold = int(match.group(1))
    qid = "kidney" if threshold <= 50 else f"kidney_{threshold}"
    question = f"Adequate kidney function (CrCl ≥ {threshold})?"
    return _rule(
        kind, text, status="unknown",
        reason="No creatinine clearance or GFR is in the record.",
        evidence="no evidence", depends_on=["kidney_adequate"],
        question_id=qid, question_text=question,
        yes_status="met", no_status="not_met", resolver="question",
    )


def _match_measurable(text: str, kind: str, patient: Patient) -> Rule | None:
    lowered = text.lower()
    if "measurable" not in lowered:
        return None
    return _rule(
        kind, text, status="unknown",
        reason="The record does not say whether disease is measurable by RECIST.",
        evidence="no evidence", depends_on=["measurable_disease"],
        question_id="measurable", question_text="Measurable disease on recent imaging?",
        yes_status="met", no_status="not_met", resolver="question",
    )


_IO = ("pembrolizumab", "nivolumab", "atezolizumab", "durvalumab", "ipilimumab", "cemiplimab", "keytruda", "opdivo", "tecentriq", "imfinzi", "checkpoint", "pd-1", "pd-l1 inhibitor", "immunotherapy")


def _match_prior_io(text: str, kind: str, patient: Patient) -> Rule | None:
    lowered = text.lower()
    if not any(token in lowered for token in ("immunotherapy", "pd-1", "pd-l1", "checkpoint", "pembrolizumab", "nivolumab", "atezolizumab")):
        return None
    if not re.search(r"prior|previous|received|treated|naive|naïve|history", lowered):
        return None
    forbidden = bool(re.search(r"no prior|naive|naïve|must not|without prior|exclusion", lowered)) or kind == "exclusion"
    required = not forbidden and bool(re.search(r"must have|previously received|prior .*immunotherap|after .*immunotherap|progress", lowered))
    if not forbidden and not required:
        return None
    if required:
        yes_status, no_status = "met", "not_met"
    else:
        yes_status, no_status = "not_met", "met"
    return _rule(
        kind, text, status="unknown",
        reason="The record does not clearly state whether the patient has had immunotherapy.",
        evidence="no evidence", depends_on=["prior_immunotherapy", "prior_treatments"],
        question_id="prior_io", question_text="Prior immunotherapy?",
        yes_status=yes_status, no_status=no_status, resolver="question",
    )


def _mentioned_drugs(lowered: str) -> list[str]:
    groups = {
        "osimertinib": ["osimertinib", "tagrisso"],
        "egfr tki": ["egfr tki", "egfr-tki", "egfr inhibitor", "third-generation", "3rd-generation", "3rd generation"],
        "trastuzumab": ["trastuzumab", "herceptin", "pertuzumab", "t-dxd", "deruxtecan"],
        "chemotherapy": ["chemotherapy", "platinum", "pemetrexed", "folfox", "folfiri"],
    }
    found = []
    for label, needles in groups.items():
        if any(needle in lowered for needle in needles):
            found.append(label)
    return found


def _patient_has_drug(patient: Patient, label: str) -> str | None:
    blob = patient.prior_text()
    aliases = {
        "osimertinib": ["osimertinib", "tagrisso"],
        "egfr tki": ["osimertinib", "tagrisso", "erlotinib", "gefitinib", "afatinib", "dacomitinib", "lazertinib", "furmonertinib", "egfr tki"],
        "trastuzumab": ["trastuzumab", "herceptin", "pertuzumab", "t-dxd", "enhertu", "deruxtecan"],
        "chemotherapy": ["chemotherapy", "folfox", "folfiri", "carboplatin", "cisplatin", "pemetrexed", "docetaxel", "paclitaxel", "platinum"],
    }
    for alias in aliases.get(label, [label]):
        if alias in blob:
            return _quote_prior(patient, alias)
    return None


def _match_prior_drug(text: str, kind: str, patient: Patient) -> Rule | None:
    lowered = norm(text).lower()
    if "washout" in lowered:
        return _rule(
            kind, text, status="unknown", reversible=True,
            reason="A washout interval is required. The record has no dates for the last dose.",
            evidence="no evidence", depends_on=["prior_treatments"], resolver="prior_drug",
        )
    if re.search(r"hypersensitivity|allergic|adverse reaction", lowered):
        return None
    drugs = _mentioned_drugs(lowered)
    if not drugs:
        return None
    naive = bool(re.search(
        r"treatment[- ]naive|previously untreated|no prior|have not (received|started)|without prior|not have received|first[- ]line",
        lowered,
    ))
    progressed = bool(re.search(r"progress|after |previously (treated|received)|who have received|following ", lowered))
    # "Progressed after first-line osimertinib" is a required prior drug, not a treatment-naive rule.
    if progressed and not re.search(r"no prior|have not|not have received|without prior|treatment[- ]naive|previously untreated", lowered):
        naive = False
    forbids = bool(re.search(r"no prior|must not|have not|not have received|without prior|previously untreated|treatment[- ]naive", lowered))
    if not naive and not progressed and not forbids:
        return None
    if kind == "exclusion" and not forbids:
        return None
    # Prefer the most specific drug mentioned.
    label = drugs[0]
    evidence = _patient_has_drug(patient, label)
    has_any_prior = bool(patient.prior_treatments) or patient.line_of_therapy not in (None, 0)
    if naive or forbids:
        if evidence:
            return _rule(
                kind, text, status="not_met",
                reason=f"The rule wants no prior {label}. The record lists {evidence}.",
                evidence=evidence, depends_on=["prior_treatments", "line_of_therapy"],
                resolver="prior_drug",
            )
        if has_any_prior or patient.line_of_therapy == 0:
            return _rule(
                kind, text, status="met",
                reason=f"Prior treatments in the record do not list {label}.",
                evidence="; ".join(patient.prior_treatments) or "No prior treatment listed",
                depends_on=["prior_treatments", "line_of_therapy"], resolver="prior_drug",
            )
        return _rule(
            kind, text, status="unknown", reason="Prior treatment history is empty.",
            evidence="no evidence", depends_on=["prior_treatments"], resolver="prior_drug",
        )
    # Required prior drug / progression.
    if evidence:
        progressed_word = "progress" in patient.prior_text()
        reason = f"The record lists {evidence}."
        if "progress" in lowered and not progressed_word:
            return _rule(
                kind, text, status="unknown",
                reason=f"The record lists {evidence}, but does not say the patient progressed on it.",
                evidence=evidence, depends_on=["prior_treatments"], resolver="prior_drug",
            )
        return _rule(
            kind, text, status="met", reason=reason, evidence=evidence,
            depends_on=["prior_treatments"], resolver="prior_drug",
        )
    if has_any_prior:
        return _rule(
            kind, text, status="not_met",
            reason=f"The rule asks for prior {label}. Recorded treatments do not list it.",
            evidence="; ".join(patient.prior_treatments),
            depends_on=["prior_treatments"], resolver="prior_drug",
        )
    return _rule(
        kind, text, status="unknown", reason="Prior treatment history is empty.",
        evidence="no evidence", depends_on=["prior_treatments"], resolver="prior_drug",
    )


def _match_line(text: str, kind: str, patient: Patient) -> Rule | None:
    lowered = text.lower()
    if not re.search(r"treatment[- ]naive|previously untreated|no prior systemic|first[- ]line only|untreated advanced", lowered):
        return None
    lines = patient.line_of_therapy
    if lines is None:
        lines = len([item for item in patient.prior_treatments if item.strip()]) or None
    if lines is None:
        return _rule(
            kind, text, status="unknown", reason="Line of therapy is not in the record.",
            evidence="no evidence", depends_on=["line_of_therapy", "prior_treatments"], resolver="line",
        )
    if lines == 0:
        return _rule(
            kind, text, status="met", reason="No prior systemic therapy is listed.",
            evidence="No prior treatment listed", depends_on=["line_of_therapy", "prior_treatments"], resolver="line",
        )
    return _rule(
        kind, text, status="not_met",
        reason=f"The study wants patients without prior systemic therapy. The record lists {lines} prior regimen(s).",
        evidence="; ".join(patient.prior_treatments) or f"{lines} prior lines",
        depends_on=["line_of_therapy", "prior_treatments"], resolver="line",
    )


_STAGE_WORDS = {
    "iiic": 3, "iiib": 3, "iiia": 3, "iii": 3,
    "iva": 4, "ivb": 4, "iv": 4,
    "iib": 2, "iia": 2, "ii": 2,
}


def _patient_stage_rank(stage: str | None) -> int | None:
    if not stage:
        return None
    token = stage.lower().replace("stage", "").strip()
    token = token.split()[0]
    return _STAGE_WORDS.get(token)


def _match_stage(text: str, kind: str, patient: Patient) -> Rule | None:
    lowered = text.lower()
    if "stage" not in lowered and "metastatic" not in lowered and "locally advanced" not in lowered:
        return None
    rank = _patient_stage_rank(patient.stage)
    if rank is None:
        return _rule(
            kind, text, status="unknown", reason="Stage is not in the record.",
            evidence="no evidence", depends_on=["stage"], resolver="stage",
        )
    mentioned = set()
    for token, value in _STAGE_WORDS.items():
        if re.search(rf"\bstage\s*{token}\b|\b{token}\b", lowered):
            mentioned.add(value)
    locally = "locally advanced" in lowered
    metastatic = "metastatic" in lowered or "advanced" in lowered
    # "advanced" alone is too broad; only count it with stage or metastatic language already handled.
    if "advanced" in lowered and "locally advanced" not in lowered and "stage" not in lowered and "metastatic" not in lowered:
        metastatic = False
    ok = False
    if rank in mentioned:
        ok = True
    if locally and rank == 3:
        ok = True
    if metastatic and rank == 4:
        ok = True
    if "unresectable" in lowered and rank >= 3:
        ok = True
    evidence = f"Stage {patient.stage}"
    if not mentioned and not locally and not metastatic:
        return None
    if ok:
        return _rule(
            kind, text, status="met",
            reason=f"Recorded stage {patient.stage} fits the stage language in this rule.",
            evidence=evidence, depends_on=["stage"], resolver="stage",
        )
    # A stage was required and the patient's stage was not among them.
    if mentioned or metastatic or locally:
        return _rule(
            kind, text, status="not_met",
            reason=f"Recorded stage {patient.stage} does not match the stage this rule requires.",
            evidence=evidence, depends_on=["stage"], resolver="stage",
        )
    return None


def _match_disease(text: str, kind: str, patient: Patient) -> Rule | None:
    lowered = text.lower()
    family = _family(patient.cancer_type)
    mentioned = []
    if re.search(r"non-small cell|non small cell|\bnsclc\b", lowered):
        mentioned.append("lung")
    elif re.search(r"\blung cancer\b", lowered):
        mentioned.append("lung")
    if re.search(r"\bbreast\b", lowered):
        mentioned.append("breast")
    if re.search(r"colorectal|colon cancer|rectal cancer|\bcrc\b", lowered):
        mentioned.append("crc")
    if not mentioned:
        return None
    if family is None:
        return _rule(
            kind, text, status="unknown", reason="Cancer type is not in the record.",
            evidence="no evidence", depends_on=["cancer_type"], resolver="disease",
        )
    if family in mentioned:
        return _rule(
            kind, text, status="met",
            reason=f"Cancer type in the record is {patient.cancer_type}.",
            evidence=patient.cancer_type or "", depends_on=["cancer_type"], resolver="disease",
        )
    if kind == "exclusion":
        return _rule(
            kind, text, status="met",
            reason=f"This exclusion names another cancer. Recorded cancer is {patient.cancer_type}.",
            evidence=patient.cancer_type or "", depends_on=["cancer_type"], resolver="disease",
        )
    return _rule(
        kind, text, status="not_met",
        reason=f"This rule requires {', '.join(mentioned)}. Recorded cancer is {patient.cancer_type}.",
        evidence=patient.cancer_type or "", depends_on=["cancer_type"], resolver="disease",
    )


def _biomarker_specs(lowered: str) -> list[dict]:
    specs = []
    if "pacc" in lowered and "egfr" in lowered:
        specs.append({"gene": "egfr", "terms": ["pacc"], "label": "EGFR PACC mutation", "negative_if": ["l858r", "exon 19", "ex19del"]})
    elif any(token in lowered for token in ("l858r", "exon 19", "ex19del", "egfr")) and re.search(r"mutation|mutant|mutated|l858r|exon 19|exon19", lowered):
        specs.append({"gene": "egfr", "terms": ["l858r", "exon 21 l858r", "exon 19", "ex19del", "exon19"], "label": "EGFR L858R or exon 19 deletion"})
    if re.search(r"\balk\b", lowered) and re.search(r"positive|rearrang|fusion|mutation", lowered):
        specs.append({"gene": "alk", "terms": ["alk positive", "alk+", "alk fusion", "alk rearrangement"], "label": "ALK positive"})
    if "g12c" in lowered:
        specs.append({"gene": "kras", "terms": ["g12c"], "label": "KRAS G12C"})
    elif "kras" in lowered and re.search(r"mutation|mutant", lowered):
        specs.append({"gene": "kras", "terms": ["kras"], "label": "KRAS mutation"})
    if re.search(r"her-?2|erbb2", lowered) and re.search(r"positive|amplified|overexpress|3\+|ihc", lowered):
        specs.append({"gene": "her2", "terms": ["her2 positive", "her2+", "her2 amplified", "her2 3", "erbb2", "her2-positive", "her2 pos"], "label": "HER2 positive"})
    if "triple" in lowered or "tnbc" in lowered:
        specs.append({"gene": "tnbc", "terms": ["triple negative", "tnbc", "triple-negative"], "label": "triple-negative breast cancer"})
    if re.search(r"msi-h|msi high|microsatellite instab", lowered):
        specs.append({"gene": "msi", "terms": ["msi-h", "msi high", "microsatellite instability"], "label": "MSI-H"})
    if "braf" in lowered and re.search(r"v600|mutation|mutant", lowered):
        specs.append({"gene": "braf", "terms": ["braf v600", "v600e", "v600"], "label": "BRAF V600"})
    pdl = re.search(r"pd-?l1[^.]{0,40}(?:>=|>|at least)\s*(\d{1,3})\s*%", lowered)
    if pdl:
        specs.append({"gene": "pdl1", "min": int(pdl.group(1)), "label": f"PD-L1 >= {pdl.group(1)}%"})
    return specs


def _match_biomarker(text: str, kind: str, patient: Patient) -> Rule | None:
    lowered = norm(text).lower()
    # Prior-therapy sentences are handled elsewhere.
    if re.search(r"prior|previously|naive|naïve|osimertinib|tki|inhibitor", lowered) and not re.search(r"mutation|l858r|exon 19|mutant|g12c", lowered):
        return None
    specs = _biomarker_specs(lowered)
    if not specs:
        return None
    blob = patient.marker_text()
    if not blob.strip():
        return _rule(
            kind, text, status="unknown", reason="No biomarkers are in the record.",
            evidence="no evidence", depends_on=["biomarkers"], resolver="biomarker",
        )
    # All listed marker requirements must hold. One miss fails the rule.
    failures = []
    hits = []
    for spec in specs:
        if "min" in spec:
            value = _pdl1(patient)
            if value is None:
                return _rule(
                    kind, text, status="unknown",
                    reason="PD-L1 percent is not in the record.",
                    evidence="no evidence", depends_on=["biomarkers"], resolver="biomarker",
                )
            if value >= spec["min"]:
                hits.append(f"PD-L1 {value}%")
            else:
                failures.append(f"PD-L1 in the record is {value}%, below {spec['min']}%.")
            continue
        if _marker_is_negative(patient, spec["gene"]) or any(term in blob for term in spec.get("negative_if", [])):
            if spec["gene"] == "egfr" and "pacc" in spec["terms"] and any(term in blob for term in ("l858r", "exon 19")):
                failures.append("Record lists a classical EGFR mutation (L858R or exon 19 deletion), and this rule asks for an EGFR PACC mutation. Confirm with the treating doctor.")
                continue
            if _marker_is_negative(patient, spec["gene"]):
                chip = _quote_marker(patient, spec["gene"]) if spec["gene"] in blob else f"{spec['gene']} negative"
                failures.append(f"Record says {chip}. This rule requires {spec['label']}.")
                continue
        found = _has_term(blob, spec["terms"])
        if not found and spec["gene"] == "her2" and ("her2" in blob and any(token in blob for token in ("pos", "3+", "amplif", "+"))):
            found = "her2"
        if not found and spec["gene"] == "alk" and "alk" in blob and any(token in blob for token in ("pos", "+", "fusion", "rearrang")):
            found = "alk"
        if found:
            hits.append(_quote_marker(patient, found))
            continue
        if spec["gene"] in blob or any(term.split()[0] in blob for term in spec["terms"]):
            failures.append(f"Biomarkers mention {spec['gene']} but not {spec['label']}.")
        else:
            failures.append(f"Biomarkers in the record do not list {spec['label']}.")
    evidence = "; ".join(patient.biomarkers)
    # An exclusion names a marker the patient must NOT have.
    if kind == "exclusion":
        if hits:
            return _rule(
                kind, text, status="not_met",
                reason=f"Record lists {hits[0]}, and this exclusion rules that out.",
                evidence=hits[0], depends_on=["biomarkers"], resolver="biomarker",
            )
        if failures:
            return _rule(
                kind, text, status="met",
                reason="Recorded biomarkers do not include the marker this exclusion lists.",
                evidence=evidence, depends_on=["biomarkers"], resolver="biomarker",
            )
    if failures:
        return _rule(
            kind, text, status="not_met", reason=failures[0],
            evidence=evidence, depends_on=["biomarkers"], resolver="biomarker",
        )
    if hits:
        return _rule(
            kind, text, status="met",
            reason=f"Patient biomarkers include {hits[0]}.",
            evidence=hits[0], depends_on=["biomarkers"], resolver="biomarker",
        )
    return None


_BOILER = (
    "informed consent", "willing to", "able to comply", "contraception", "pregnant",
    "breastfeeding", "breast-feeding", "investigator", "other malignancy", "hiv",
    "hepatitis", "qtc", "ejection fraction", "lveft", "absolute neutrophil",
    "platelet", "hemoglobin", "bilirubin", "aspartate", "alanine", "organ function",
    "birth control",
)


def _match_boilerplate(text: str, kind: str, patient: Patient) -> Rule | None:
    lowered = text.lower()
    if not any(token in lowered for token in _BOILER):
        return None
    return _rule(
        kind, text, status="unknown", boilerplate=True,
        reason="This is a standard procedural or lab rule. The record does not contain it.",
        evidence="no evidence", depends_on=[], resolver="boilerplate",
    )


_MATCHERS: list[Matcher] = [
    _match_biomarker,
    _match_prior_drug,
    _match_prior_io,
    _match_line,
    _match_scan,
    _match_brain,
    _match_kidney,
    _match_measurable,
    _match_stage,
    _match_ecog,
    _match_age,
    _match_sex,
    _match_disease,
    _match_boilerplate,
]


def judge_rule(kind: str, text: str, patient: Patient, rule_id: str) -> Rule:
    """Combine every topic the bullet actually mentions.

    A failure wins. An unknown wins over a pass. A pass is used only when
    every recognized topic passes. Unrecognized bullets stay unknown.
    """
    hits = [matcher(text, kind, patient) for matcher in _MATCHERS]
    hits = [hit for hit in hits if hit is not None]
    if not hits:
        judged = _rule(
            kind, text, status="unknown",
            reason="The record does not contain evidence for this rule.",
            evidence="no evidence", depends_on=[], resolver="unknown",
        )
    else:
        fails = [hit for hit in hits if hit.status == "not_met"]
        unknowns = [hit for hit in hits if hit.status == "unknown" and hit.resolver != "boilerplate"]
        if fails:
            judged = fails[0].model_copy(deep=True)
        elif unknowns:
            judged = unknowns[0].model_copy(deep=True)
        else:
            judged = hits[0].model_copy(deep=True)
        depends: list[str] = []
        for hit in hits:
            for field in hit.depends_on:
                if field not in depends:
                    depends.append(field)
        judged.depends_on = depends
        if any(hit.boilerplate for hit in hits) and judged.status == "unknown":
            judged.boilerplate = True
    judged.id = rule_id
    judged.text = text
    judged.kind = kind  # type: ignore[assignment]
    return judged


def evaluate_trial(patient: Patient, criteria_text: str, *, provider: str = "stub") -> TrialCheck:
    raw = split_criteria(criteria_text)
    if not raw and criteria_text.strip():
        raw = [("inc-fallback", "inclusion", criteria_text.strip()[:500])]
    rules = [judge_rule(kind, text, patient, rule_id) for rule_id, kind, text in raw]
    if not rules:
        rules = [
            _rule(
                "inclusion",
                "Eligibility criteria text was not available from ClinicalTrials.gov.",
                status="unknown",
                reason="ClinicalTrials.gov did not include eligibility text for this study.",
                evidence="no evidence",
                depends_on=[],
                resolver="unknown",
                rule_id="missing-criteria",
            )
        ]
    return TrialCheck(nct_id="", rules=rules, provider=provider)


def counts(rules: list[Rule]) -> tuple[int, int, int]:
    met = sum(1 for rule in rules if rule.status == "met")
    failed = sum(1 for rule in rules if rule.status == "not_met")
    unknown = sum(1 for rule in rules if rule.status == "unknown")
    return met, failed, unknown


def apply_overrides(rules: list[Rule], answers: dict[str, str], what_ifs: dict[str, bool]) -> list[Rule]:
    """Apply doctor answers and hypothetical switches without a new model call."""
    scan_on = bool(what_ifs.get("scan_within_window"))
    updated: list[Rule] = []
    for rule in rules:
        status = rule.status
        reason = rule.reason
        evidence = rule.evidence
        hypothetical = False
        answer = answers.get(rule.question_id or "")
        if answer == "yes" and rule.yes_status:
            status = rule.yes_status
            reason = "Doctor answered yes. " + rule.reason
            evidence = "Doctor answer: yes"
        elif answer == "no" and rule.no_status:
            status = rule.no_status
            reason = "Doctor answered no. " + rule.reason
            evidence = "Doctor answer: no"
        if scan_on and rule.resolver == "scan_within_days" and status == "not_met":
            status = "met"
            hypothetical = True
            reason = "Hypothetical: a new scan falls inside the trial window. Original finding: " + rule.reason
            evidence = "What-if: new scan within the trial window"
        copy = rule.model_copy(update={"status": status, "reason": reason, "evidence": evidence})
        copy.reversible = rule.reversible or hypothetical
        # Stash hypothetical on boilerplate? Use resolver field? We'll use a side channel
        # via question_text prefix? Better add a transient attribute. Pydantic model
        # doesn't have hypothetical. Encode it by prefixing reason, and the view checks reason.
        if hypothetical:
            copy = copy.model_copy(update={"resolver": "scan_within_days_hypothetical"})
        updated.append(copy)
    return updated


def tag_for(failed: int, index: int, reversible_single: bool) -> str | None:
    if failed == 0 and index == 0:
        return "best_fit"
    if failed == 1 and reversible_single:
        return "near_miss"
    if failed == 1:
        return "near_miss"
    if failed >= 2:
        return "excluded"
    return None


def rescore_rules(rules: list[Rule], patient: Patient, changed: set[str]) -> list[Rule]:
    """Re-judge only rules that depend on an edited fact. Others stay as stored."""
    if not changed:
        return rules
    fresh: list[Rule] = []
    for rule in rules:
        if rule.resolver == "question":
            fresh.append(rule)
            continue
        if not (set(rule.depends_on) & changed):
            fresh.append(rule)
            continue
        judged = judge_rule(rule.kind, rule.text, patient, rule.id)
        fresh.append(judged)
    return fresh


def questions_from(checks: list[tuple[str, list[Rule]]], answers: dict[str, str]) -> list[dict]:
    bucket: dict[str, dict] = {}
    for nct_id, rules in checks:
        seen: set[str] = set()
        for rule in rules:
            if not rule.question_id or rule.question_id in seen:
                continue
            # A question matters when the base rule is unknown (the answer supplies the fact).
            if rule.resolver != "question" and rule.status != "unknown":
                continue
            if rule.yes_status is None and rule.no_status is None:
                continue
            seen.add(rule.question_id)
            slot = bucket.setdefault(
                rule.question_id,
                {"id": rule.question_id, "text": rule.question_text or rule.question_id, "trials": set()},
            )
            slot["trials"].add(nct_id)
    items = []
    for slot in bucket.values():
        answer = answers.get(slot["id"], "unknown")
        items.append(
            {
                "id": slot["id"],
                "text": slot["text"],
                "affects": len(slot["trials"]),
                "answer": answer,
            }
        )
    items.sort(key=lambda item: (-item["affects"], item["text"]))
    return items[:5]


def what_ifs_from(rows: list[dict], current: dict[str, bool]) -> list[dict]:
    """Build hypothetical switches from reversible single-rule failures."""
    affected = 0
    unlock = 0
    scan_days = None
    for row in rows:
        # Base failures ignore the what-if so the switch does not erase itself.
        base_fails = row.get("base_failures") or []
        scan_fails = [item for item in base_fails if item.get("resolver") == "scan_within_days"]
        if not scan_fails:
            continue
        affected += 1
        if len(base_fails) == 1:
            unlock += 1
        match = re.search(r"within (\d+) days", scan_fails[0].get("reason") or "")
        if match:
            found = int(match.group(1))
            scan_days = found if scan_days is None else min(scan_days, found)
    items = []
    if affected:
        days = scan_days or 28
        if unlock:
            detail = f"Would unlock {unlock} more trial{'s' if unlock != 1 else ''} (hypothetical)"
        else:
            detail = f"Would clear an imaging-window failure on {affected} trial{'s' if affected != 1 else ''} (hypothetical)"
        items.append(
            {
                "id": "scan_within_window",
                "label": f"What if: new scan within {days} days",
                "detail": detail,
                "on": bool(current.get("scan_within_window")),
                "hypothetical": True,
            }
        )
    return items


def phase_numbers(phases: list[str]) -> set[str]:
    found: set[str] = set()
    for phase in phases:
        token = phase.upper()
        if "4" in token:
            found.add("4")
        if "3" in token:
            found.add("3")
        if "2" in token:
            found.add("2")
        if "1" in token:
            found.add("1")
    return found


def phase_allowed(phases: list[str], selected: list[str]) -> bool:
    if not selected:
        return True
    numbers = phase_numbers(phases)
    if not numbers:
        return True
    return bool(numbers & set(selected))


def short_text(text: str, limit: int = 110) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"
