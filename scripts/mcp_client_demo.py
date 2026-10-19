#!/usr/bin/env python3
"""Run the TrialRadar MCP tools the way a bot would.

Uses whatever DEMO_CACHE / LLM_PROVIDER are already set. Prints a short transcript.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

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


async def main() -> None:
    env = os.environ.copy()
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "trialradar.mcp_server"],
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools:", ", ".join(tool.name for tool in tools.tools))
            extracted = _payload(await session.call_tool("extract_patient", {"note_text": NOTE}))
            print("patient:", extracted["patient"]["cancer_type"], extracted["patient"]["city"])
            found = _payload(await session.call_tool("find_trials", {}))
            print("found:", found.get("found"), "source:", found.get("source"), "error:", found.get("error"))
            checked = _payload(await session.call_tool("check_eligibility", {"top_n": 5}))
            for row in checked.get("ranked") or []:
                print(f"  #{row['rank']} {row['nct_id']} met={row['met']} failed={row['failed']} unknown={row['unknown']} {row.get('tag')}")
            notes = _payload(await session.call_tool("draft_notes", {}))
            print(notes.get("summary", notes.get("error")))


if __name__ == "__main__":
    asyncio.run(main())
