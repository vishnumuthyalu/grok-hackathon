"""python -m trialradar serve | mcp | record-cache"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="trialradar", description="TrialRadar local server")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run the web UI and JSON API")
    serve.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))

    mcp = sub.add_parser("mcp", help="Run the MCP server")
    mcp.add_argument("--http", action="store_true", help="Streamable HTTP instead of stdio")

    sub.add_parser("record-cache", help="Record the demo cache from live ClinicalTrials.gov")

    args = parser.parse_args()
    command = args.command or "serve"
    if command == "mcp":
        if args.http:
            sys.argv.append("--http")
        from trialradar.mcp_server import main as mcp_main

        mcp_main()
        return
    if command == "record-cache":
        from trialradar.record import main as record_main

        record_main()
        return
    import uvicorn

    uvicorn.run("trialradar.api:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
