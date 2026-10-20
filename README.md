# TrialRadar

TrialRadar finds **real, currently recruiting** clinical trials for an adult cancer patient and explains, rule by rule, why each one fits. It is built for a community oncology visit: non-small cell lung cancer (EGFR, ALK, KRAS G12C), metastatic breast cancer (HER2-positive or triple-negative), and metastatic colorectal cancer (MSI, KRAS, BRAF).

The primary user is an AI assistant. A bot that has never seen the product can read the MCP tool descriptions and do the whole job. Doctors and judges use the web app on the **same session**, so a bot on the left and the TrialRadar window on the right stay in sync.

Everything runs on one laptop. Trial lists come from the public ClinicalTrials.gov API v2 (no key). The optional language model is local Ollama. Nothing is sent to a patient or a trial site.

**Suggestions for the treating doctor to confirm. TrialRadar never contacts patients or sites.**

## One-command setup

```bash
./scripts/setup.sh
```

That creates `.venv`, installs Python packages, and:

| Tool | What the script does |
| --- | --- |
| Tesseract | Uses it if present. Otherwise tries `brew install tesseract` (macOS) or `apt-get` / `dnf` (Linux). Text PDFs work without it. |
| Ollama | If `ollama` is on the PATH, pulls `qwen2.5:7b` (override with `OLLAMA_MODEL`). |

Install Ollama itself from [ollama.com](https://ollama.com) if you want the model path. The demo does not need it when `DEMO_CACHE=1`.

## Run the app

```bash
./scripts/run.sh
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). `run.sh` defaults to `DEMO_CACHE=1` and `LLM_PROVIDER=ollama`, and falls back to the local rules engine if Ollama is not running.

Load a prepared patient from **Load example** (NSCLC EGFR L858R · Austin, HER2-positive breast · Houston, or colorectal KRAS G12C · Dallas).

MCP server, same data directory, for a bot:

```bash
python -m trialradar.mcp_server          # stdio, what Goose and Cursor launch
python -m trialradar.mcp_server --http   # optional streamable HTTP on 127.0.0.1:8001
```

Record a fresh demo cache from live ClinicalTrials.gov (needs a network; Ollama optional):

```bash
LLM_PROVIDER=stub python scripts/record_cache.py
# or: python -m trialradar record-cache
```

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `DEMO_CACHE` | `1` in `run.sh` | Serve recorded ClinicalTrials.gov results for the 3 prepared patients. Other patients are looked up live. |
| `DEMO_CACHE_PATH` | `data/demo_cache.json` | Recorded studies and rule judgments. |
| `LLM_PROVIDER` | `ollama` | `ollama` or `stub` (local rules engine, no model). |
| `LLM_FALLBACK` | `stub` | Used when Ollama is not reachable. The UI says so. |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama server. |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Text model. One call scores every rule of one trial. |
| `OLLAMA_VISION_MODEL` | `qwen2.5vl:7b` | Optional. Used only for a photo when this model is installed. |
| `CHECK_TOP_N` | `5` | How many trials to score per pass. |
| `FETCH_RADIUS_MI` | `250` | Retrieved once so the distance slider can move without a new search. |
| `TRIALRADAR_DATA` | absolute `data/` from `run.sh` and `demo.sh` | Shared session file. The UI and the MCP process both use this path. |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | Web UI. |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8001` | Streamable HTTP MCP. |
| `NOMINATIM_USER_AGENT` | a local-demo string | Only used after the hardcoded city list misses. At most 1 request/second, cached. |

Copy `.env.example` if you want the full list in one place.

## Connect a bot

Tools, in order: `extract_patient` → `find_trials` → `check_eligibility` → `rescore` → `draft_notes`.

Each description says what to pass, what comes back, and what to call next. Every payload includes the disclaimer. `draft_notes` is copy or download only.

**Goose.** Merge `demo/goose/config.yaml` into `~/.config/goose/config.yaml`. Point `cmd` at the venv Python if `python` is not the project environment. Set the provider to Ollama and `qwen2.5:7b`.

**Cursor.** Use `demo/cursor/mcp.json` as `.cursor/mcp.json` (or merge it into your user MCP config). Start it from the repo root so `python -m trialradar.mcp_server` imports this project.

Side-by-side with the browser (the bot waits for Enter before each tool):

```bash
./scripts/demo.sh
```

`./scripts/demo.sh --delay 2` pauses two seconds instead of waiting for Enter. `--fast` runs every tool back to back. Both `run.sh` and the demo script set `TRIALRADAR_DATA` to this repo's `data/` directory as an absolute path, so the web app and the MCP process share one session file. The page follows it on `/api/events`.

Try this as the bot's task:

> Read demo/reports/nsclc_egfr_austin.txt, find recruiting trials within 100 miles, check the top 5, and draft a referral note and a plain-language summary. Do not contact anyone.

## Three-minute demo

Browser on the right, the stepped bot in a terminal on the left. `DEMO_CACHE=1` so this works offline after setup.

```bash
./scripts/demo.sh
```

That starts the server if it is not already up, opens [http://127.0.0.1:8000](http://127.0.0.1:8000), clears the previous session so the page starts empty, and runs `scripts/mcp_client_demo.py` in step mode. Press Enter before each tool. The page updates after that tool writes the shared session.

1. **extract_patient** reads the Austin NSCLC note. The form fills in: 62 F, stage IIIB, EGFR L858R, osimertinib progressed, Austin, last scan 47 days ago. The trial list is still empty.
2. **find_trials** lists recruiting studies within 100 miles, marked “Not checked”.
3. **check_eligibility** scores the top 5. Rank 1 is the best fit (no failed rule). A near miss is exactly one failed rule, with the blocking line and what would change it. The first-line osimertinib study fails because this patient already received an EGFR TKI.
4. **rescore** answers **Brain metastases on recent MRI?** with No. The question flips on the left. No new search and no model call.
5. **draft_notes** puts the plain-language summary on the trial panel. Copy or download only. Nothing is sent.

Then the judge takes over the same page:

1. Click a trial and read the evidence quotes. Unknown rules are questions on the left (brain MRI, measurable disease).
2. The brain-MRI question may already say No. Change it, or leave it. The list re-ranks with no new search and no model call.
3. Flip **What if: new scan within 14 days**. The label is hypothetical. The number comes from the real criterion (this cache uses 14 days, not a made-up 28).
4. Change the biomarker chip from `EGFR L858R` to `EGFR none` and press Enter. Trials that require an EGFR mutation drop. Still no new search.
5. Drag the distance slider. Phase checkboxes and **Recruiting only** filter the list that was already retrieved.
6. **Compare with #2** shows both trials rule by rule. **Draft referral note** and **Patient-friendly summary** open editable text. Copy or download. There is no send button.
7. The footer stays on screen: suggestions for the treating doctor; TrialRadar never contacts patients or sites.

**Refresh live** in the filter bar ignores the demo cache and calls ClinicalTrials.gov.

## Cut list if time is short

Ship in this order. Stop when the clock runs out.

1. Demo cache + the three patients + the web UI (offline demo).
2. MCP tools and `demo/cursor/mcp.json` (the bot track).
3. Doctor questions, what-if, compare, draft notes.
4. Live ClinicalTrials.gov (`DEMO_CACHE=0` or **Refresh live**).
5. Ollama (`ollama pull qwen2.5:7b`). Until then the rules engine is the checker and the UI says so.
6. Photo OCR (Tesseract) and the optional vision model.
7. Nominatim. Hardcoded cities already cover the demo.
8. The map. The rest of the app does not need tiles.

## Three-person split

| Person | Owns |
| --- | --- |
| A. Data | `ctgov.py`, `geo.py`, `criteria.py`, `eligibility.py`, `scripts/record_cache.py` |
| B. Bot | `mcp_server.py`, session file, demo cache, Goose and Cursor configs, `scripts/mcp_client_demo.py` |
| C. UI | `web/`, synthetic reports in `demo/reports/`, this demo script |

Shared contract: `service.py` and `models.py`. Both the API and the MCP server call it, and both read `data/session.json`.

## How a check stays fast

- ClinicalTrials.gov is queried once, out to `FETCH_RADIUS_MI`, then filtered locally.
- Only the top N trials are scored. One model call covers every rule of that trial (Ollama structured JSON). The stub provider does the same in one function call.
- Each rule stores the patient fields it used. An edit re-judges only those rules. Answers, what-if switches, and the distance slider do not call the model.
- `DEMO_CACHE=1` replays recorded ClinicalTrials.gov studies for the three prepared patients. Judgments in that file were produced by the recorder; they are not hand-written trials.

Trial text, NCT ids, titles, and site coordinates are copied from ClinicalTrials.gov. The rules engine returns **unknown** when the record does not contain the fact. It does not invent labs, scans, or biomarkers.

## Synthetic reports

`demo/reports/` has HTML, plain text, and PDF for:

- NSCLC, EGFR L858R, post-osimertinib, Austin, last scan 47 days ago, no brain MRI
- HER2-positive metastatic breast cancer after two lines, Houston
- Metastatic colorectal cancer, KRAS G12C, Dallas
- A blank template

Each file is stamped **SYNTHETIC – FOR DEMO**. They follow a CAP-style synoptic layout and are not real patients.

## Tests

```bash
python -m pytest
```

The tests use the local rules engine and a recorded ClinicalTrials.gov study fixture (`tests/fixtures/nct04486833.json`). They do not need Ollama. `tests/test_mcp.py` starts the MCP server over stdio and runs extract → find → check → rescore → draft.

The MCP test launches its server with the test runner's Python interpreter by default. Set `PYTHON` to override that interpreter explicitly.

## Layout

```
src/trialradar/    engine, ClinicalTrials.gov client, MCP server, FastAPI
web/               the doctor UI (HTML, CSS, Leaflet)
demo/              reports, Goose config, Cursor mcp.json
data/demo_cache.json
scripts/           setup, run, record cache, MCP demo client
tests/
```

## Safety

This is a hackathon aid for a treating doctor and their assistant. It is not a medical device, not an offer to join a study, and not a message to a trial site. Drafts stay on the laptop unless a person copies them.
