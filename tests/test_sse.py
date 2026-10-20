"""The open page follows session writes from another process."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

UI_MESSAGE = "Start ./scripts/run.sh and open http://127.0.0.1:8000 first"


def test_demo_script_tells_you_to_start_the_ui():
    text = Path("scripts/mcp_client_demo.py").read_text()
    assert UI_MESSAGE in text
    assert "--fast" in text
    assert "brain_mets" in text


def test_events_follow_writes_from_another_process(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setenv("TRIALRADAR_DATA", str(shared))
    from trialradar.api import app

    seen: list[dict] = []
    ready = threading.Event()

    def listen():
        with TestClient(app) as client:
            with client.stream("GET", "/api/events") as response:
                ready.set()
                for line in response.iter_lines():
                    if isinstance(line, bytes):
                        line = line.decode()
                    if not line.startswith("data: "):
                        continue
                    seen.append(json.loads(line[6:]))
                    if any(item.get("answers", {}).get("brain_mets") == "no" for item in seen):
                        return

    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    assert ready.wait(5), "event stream did not open"

    script = """
import time
from trialradar.models import Patient
from trialradar.session import load, reset, save
reset()
time.sleep(0.8)
session = load()
session.patient = Patient(age=62, sex="F", cancer_type="Non-small cell lung cancer", city="Austin, TX")
session.patient_source = "mcp"
save(session)
time.sleep(0.8)
session = load()
session.source_label = "step-find"
save(session)
time.sleep(0.8)
session = load()
session.answers = {"brain_mets": "no"}
save(session)
time.sleep(1.2)
"""
    env = os.environ.copy()
    env["TRIALRADAR_DATA"] = str(shared)
    subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    thread.join(8)
    assert any(item.get("patient") and item["patient"]["city"].startswith("Austin") for item in seen)
    assert any(item.get("source_label") == "step-find" for item in seen)
    assert any(item.get("answers", {}).get("brain_mets") == "no" for item in seen)
    revisions = [item["revision"] for item in seen]
    assert revisions == sorted(revisions)
    assert revisions[-1] > revisions[0]
    patient_at = next(index for index, item in enumerate(seen) if item.get("patient"))
    assert any(item.get("patient") is None for item in seen[:patient_at])
