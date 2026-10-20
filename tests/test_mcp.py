"""Scripted MCP client. Spawns the stdio server and runs the doctor workflow."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from trialradar.cache import signature, write_cache
from trialradar.ctgov import parse_study
from trialradar.demo_patients import NSCLC_AUSTIN

FIXTURE = json.loads(Path("tests/fixtures/nct04486833.json").read_text())
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


def _prepare(tmp_path, monkeypatch):
    trial = parse_study(FIXTURE)
    trial.sites[0].city = "Austin"
    trial.sites[0].state = "Texas"
    trial.sites[0].status = "RECRUITING"
    trial.sites[0].lat = 30.2672
    trial.sites[0].lon = -97.7431
    cache = tmp_path / "demo_cache.json"
    data = tmp_path / "session-data"
    data.mkdir()
    monkeypatch.setenv("DEMO_CACHE_PATH", str(cache))
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
    env = os.environ.copy()
    env.update({
        "LLM_PROVIDER": "stub",
        "DEMO_CACHE": "1",
        "DEMO_CACHE_PATH": str(cache),
        "TRIALRADAR_DATA": str(data),
        "LLM_FALLBACK": "stub",
    })
    return env


def _text(result) -> dict:
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    chunks = []
    for block in result.content:
        if getattr(block, "text", None):
            chunks.append(block.text)
    raw = "\n".join(chunks)
    return json.loads(raw)


def test_mcp_client_does_the_whole_job(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    asyncio.run(_mcp_roundtrip(tmp_path, monkeypatch))


async def _mcp_roundtrip(tmp_path, monkeypatch):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = _prepare(tmp_path, monkeypatch)
    params = StdioServerParameters(
        command=os.environ.get("PYTHON", sys.executable),
        args=["-m", "trialradar.mcp_server"],
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            assert names == [
                "extract_patient",
                "find_trials",
                "check_eligibility",
                "rescore",
                "draft_notes",
            ]
            for tool in tools.tools:
                assert len(tool.description) > 80
                assert tool.inputSchema.get("type") == "object"

            extracted = _text(await session.call_tool("extract_patient", {"note_text": NOTE}))
            assert extracted["patient"]["age"] == 62
            assert extracted["patient"]["city"].startswith("Austin")
            assert "Suggestions for the treating doctor" in extracted["disclaimer"]

            found = _text(await session.call_tool("find_trials", {"distance_mi": 100}))
            assert found["error"] in (None, "")
            assert any(item["nct_id"] == "NCT04486833" for item in found["trials"])
            assert "ClinicalTrials.gov" in found["source"] or found["demo_cache"] is True

            checked = _text(await session.call_tool("check_eligibility", {"top_n": 5}))
            assert checked["ranked"]
            row = checked["ranked"][0]
            assert row["nct_id"] == "NCT04486833"
            assert row["met"] >= 1
            assert any(rule["evidence"] != "no evidence" or rule["status"] == "unknown" for rule in row["rules"])
            assert "Suggestions for the treating doctor" in checked["disclaimer"]

            edited = extracted["patient"]
            edited["biomarkers"] = ["EGFR none", "PD-L1 40%"]
            scored = _text(await session.call_tool("rescore", {
                "patient": edited,
                "answers": {"brain_mets": "no"},
                "what_ifs": {"scan_within_window": True},
                "distance_mi": 50,
            }))
            assert scored["ranked"][0]["failed"] >= 1
            assert scored["disclaimer"]

            notes = _text(await session.call_tool("draft_notes", {"nct_id": "NCT04486833"}))
            assert "NOT SENT" in notes["referral"]
            assert notes["delivery"] == "copy_or_download_only"
            assert "does not contact" in notes["summary"]
