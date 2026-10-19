#!/usr/bin/env python3
"""Write the synthetic CAP-style demo reports as HTML and PDF."""

from __future__ import annotations

import html
from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo" / "reports"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

REPORTS = {
    "nsclc_egfr_austin": {
        "title": "NSCLC adenocarcinoma, EGFR L858R",
        "lines": [
            "Patient name: Synthetic Patient A",
            "Age: 62",
            "Sex: Female",
            "Home city: Austin, TX",
            "Cancer type: Non-small cell lung cancer",
            "Histologic type: Adenocarcinoma",
            "Stage: IIIB (AJCC 8th edition)",
            "ECOG performance status: 1",
            "Biomarkers: EGFR exon 21 L858R mutation detected; ALK not detected; PD-L1 TPS 40%",
            "Prior immunotherapy: No",
            "Prior treatment: Osimertinib (progressed)",
            "Last imaging: 47 days before this note",
            "Brain MRI: not in record",
            "Notes: Progressed on first-line osimertinib. No creatinine clearance in this note.",
        ],
    },
    "breast_her2_houston": {
        "title": "Metastatic HER2-positive breast cancer",
        "lines": [
            "Patient name: Synthetic Patient B",
            "Age: 54",
            "Sex: Female",
            "Home city: Houston, TX",
            "Cancer type: Breast cancer",
            "Histologic type: Invasive ductal carcinoma",
            "Stage: IV (AJCC 8th edition)",
            "ECOG performance status: 1",
            "Biomarkers: HER2 IHC 3+ amplified; ER negative; PR negative",
            "Prior immunotherapy: No",
            "Prior treatment: Trastuzumab + pertuzumab + docetaxel; T-DXd (progressed)",
            "Last imaging: 21 days before this note",
            "Brain MRI: negative",
            "Notes: Metastatic HER2-positive breast cancer after two lines of therapy.",
        ],
    },
    "colorectal_kras_dallas": {
        "title": "Metastatic colorectal adenocarcinoma, KRAS G12C",
        "lines": [
            "Patient name: Synthetic Patient C",
            "Age: 58",
            "Sex: Male",
            "Home city: Dallas, TX",
            "Cancer type: Colorectal cancer",
            "Histologic type: Adenocarcinoma",
            "Stage: IV (AJCC 8th edition)",
            "ECOG performance status: 0",
            "Biomarkers: KRAS G12C; MSS; BRAF wild-type",
            "Prior immunotherapy: No",
            "Prior treatment: FOLFOX + bevacizumab; FOLFIRI (progressed)",
            "Last imaging: 18 days before this note",
            "Brain MRI: not in record",
            "Notes: Metastatic colorectal adenocarcinoma. Microsatellite stable. No creatinine clearance in this note.",
        ],
    },
    "blank_template": {
        "title": "Blank oncology summary",
        "lines": [
            "Patient name:",
            "Age:",
            "Sex:",
            "Home city:",
            "Cancer type:",
            "Histologic type:",
            "Stage:",
            "ECOG performance status:",
            "Biomarkers:",
            "Prior immunotherapy:",
            "Prior treatment:",
            "Last imaging:",
            "Brain MRI:",
            "Notes:",
        ],
    },
}


def _html(title: str, lines: list[str]) -> str:
    rows = "\n".join(f"<tr><td>{html.escape(line.split(':', 1)[0])}</td><td>{html.escape(line.split(':', 1)[1].strip())}</td></tr>" for line in lines)
    body = "\n".join(html.escape(line) for line in lines)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body {{ font-family: Georgia, serif; max-width: 760px; margin: 32px auto; color: #1b2733; }}
.stamp {{ border: 3px solid #8a5a00; color: #8a5a00; font-weight: 700; letter-spacing: .08em;
  display: inline-block; padding: 6px 10px; margin-bottom: 12px; }}
h1 {{ font-size: 22px; }}
table {{ border-collapse: collapse; width: 100%; }}
td {{ border-bottom: 1px solid #e2e7ec; padding: 6px 8px; vertical-align: top; }}
td:first-child {{ width: 34%; color: #5f6e7c; font-size: 13px; text-transform: uppercase; letter-spacing: .04em; }}
.pre {{ white-space: pre-wrap; font-family: ui-monospace, monospace; font-size: 13px; background: #f7f9fa; padding: 12px; }}
</style></head><body>
<div class="stamp">SYNTHETIC – FOR DEMO</div>
<p>Not a real patient. Do not use for care. Modeled on a CAP synoptic layout. TrialRadar never contacts patients or sites.</p>
<h1>{html.escape(title)}</h1>
<table>{rows}</table>
<h2>Plain text the extractor reads</h2>
<div class="pre">{body}</div>
</body></html>
"""


def _pdf(path: Path, title: str, lines: list[str]) -> None:
    pdf = FPDF(format="Letter")
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.add_font("DejaVu", "", FONT)
    pdf.add_font("DejaVu", "B", FONT_B)
    pdf.set_text_color(138, 90, 0)
    pdf.set_font("DejaVu", "B", 14)
    pdf.multi_cell(0, 8, "SYNTHETIC - FOR DEMO", align="C", border=1, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_text_color(27, 39, 51)
    pdf.set_font("DejaVu", "B", 16)
    pdf.multi_cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 10)
    pdf.multi_cell(0, 6, "Not a real patient. Do not use for care. Suggestions for the treating doctor to confirm.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("DejaVu", "", 12)
    for line in lines:
        pdf.multi_cell(0, 7, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(path)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for key, report in REPORTS.items():
        (OUT / f"{key}.html").write_text(_html(report["title"], report["lines"]))
        (OUT / f"{key}.txt").write_text("SYNTHETIC – FOR DEMO\n" + "\n".join(report["lines"]) + "\n")
        _pdf(OUT / f"{key}.pdf", report["title"], report["lines"])
        print(key)


if __name__ == "__main__":
    main()
