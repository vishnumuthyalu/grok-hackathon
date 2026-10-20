#!/usr/bin/env python3
"""Step through the TrialRadar MCP tools the way a bot would.

Default is step mode: before each tool, print what is about to happen and
wait for Enter so the browser can show that step. --delay N waits N seconds
instead. --fast runs every tool immediately.

The web app has to be open first. This script and ./scripts/run.sh both use
the repo data/ directory (absolute path) as TRIALRADAR_DATA.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("TRIALRADAR_DATA", str(ROOT / "data"))
os.environ.setdefault("DEMO_CACHE", "1")
os.environ.setdefault("LLM_FALLBACK", "stub")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

UI = "http://127.0.0.1:8000"
UI_MESSAGE = "Start ./scripts/run.sh and open http://127.0.0.1:8000 first"

NOTE = """Age: 62
Sex: Female
Cancer type: Non-small cell lung cancer
Stage: IIIB
ECOG performance status: 1
Home city: Austin, TX
Biomarkers: EGFR exon 21 L858R mutation detected; PD-L1 TPS 40%
Prior treatment: Osimertinib (progressed)
Prior immunotherapy: No
Last imaging: 47 days before this note
Brain MRI: not in record
"""


def _payload(result) -> dict:
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    text = "\n".join(block.text for block in result.content if getattr(block, "text", None))
    return json.loads(text)


def _ui_up() -> bool:
    try:
        with urllib.request.urlopen(UI + "/api/health", timeout=1) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def wait_for_ui() -> None:
    if _ui_up():
        return
    print(UI_MESSAGE, flush=True)
    while not _ui_up():
        time.sleep(0.5)


def clear_session() -> None:
    from trialradar.session import reset

    fresh = reset()
    print(
        f"Cleared the shared session (revision {fresh.revision}). "
        f"The page at {UI} should be empty.",
        flush=True,
    )


def pause(action: str, *, fast: bool, delay: float | None) -> None:
    print(f"\nBot next: {action}", flush=True)
    if fast:
        return
    if delay is not None:
        print(f"Waiting {delay:g}s so the browser can update…", flush=True)
        time.sleep(delay)
        return
    try:
        input("Press Enter to run this step… ")
    except EOFError:
        print("(no terminal input; continuing)", flush=True)


def _answers_with_brain_mri(checked: dict) -> dict:
    answers = {}
    for question in checked.get("questions") or []:
        if question.get("answer") in {"yes", "no", "unknown"}:
            answers[question["id"]] = question["answer"]
    answers["brain_mets"] = "no"
    return answers


def _print_ranked(payload: dict) -> None:
    for row in payload.get("ranked") or []:
        print(
            f"  #{row['rank']} {row['nct_id']} met={row['met']} failed={row['failed']} "
            f"unknown={row['unknown']} {row.get('tag')}",
            flush=True,
        )


async def run(fast: bool, delay: float | None) -> None:
    wait_for_ui()
    clear_session()
    env = os.environ.copy()
    env["TRIALRADAR_DATA"] = os.environ["TRIALRADAR_DATA"]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "trialradar.mcp_server"],
        env=env,
        cwd=str(ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools:", ", ".join(tool.name for tool in tools.tools), flush=True)

            pause("extract_patient — read the Austin NSCLC note into the form.", fast=fast, delay=delay)
            extracted = _payload(await session.call_tool("extract_patient", {"note_text": NOTE}))
            patient = extracted.get("patient") or {}
            print(f"patient: {patient.get('cancer_type')} · {patient.get('city')}", flush=True)

            pause("find_trials — recruiting studies within 100 miles. Not scored yet.", fast=fast, delay=delay)
            found = _payload(await session.call_tool("find_trials", {}))
            print(
                f"found: {found.get('found')} checked: {found.get('checked')} "
                f"source: {found.get('source')} error: {found.get('error')}",
                flush=True,
            )

            pause("check_eligibility — score the top 5, every rule in one pass.", fast=fast, delay=delay)
            checked = _payload(await session.call_tool("check_eligibility", {"top_n": 5}))
            _print_ranked(checked)

            pause(
                "rescore — answer “Brain metastases on recent MRI?” with No. No new search.",
                fast=fast,
                delay=delay,
            )
            scored = _payload(await session.call_tool("rescore", {"answers": _answers_with_brain_mri(checked)}))
            print("brain MRI answer is now No.", flush=True)
            _print_ranked(scored)

            pause("draft_notes — referral note and plain-language summary. Nothing is sent.", fast=fast, delay=delay)
            notes = _payload(await session.call_tool("draft_notes", {}))
            print(notes.get("summary") or notes.get("error"), flush=True)
            print("Done. The drafts are on the page. Nothing was sent.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Step the TrialRadar bot while the web UI watches.")
    parser.add_argument("--fast", action="store_true", help="Run every tool immediately, without waiting.")
    parser.add_argument("--delay", type=float, default=None, metavar="N", help="Wait N seconds before each tool instead of Enter.")
    args = parser.parse_args()
    if args.delay is not None and args.delay < 0:
        parser.error("--delay must be >= 0")
    asyncio.run(run(fast=args.fast, delay=args.delay))


if __name__ == "__main__":
    main()
