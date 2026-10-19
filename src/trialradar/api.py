"""FastAPI app: the doctor UI, JSON API, and server-sent events."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from trialradar import DISCLAIMER, __version__
from trialradar.llm import ollama_reachable
from trialradar.models import Filters, Patient
from trialradar.paths import uploads_dir, web_dir
from trialradar.service import (
    _demo_enabled,
    check,
    draft,
    examples,
    extract,
    load_example,
    rescore,
    search,
    select,
    view,
)
from trialradar.session import load

app = FastAPI(title="TrialRadar", version=__version__)


class SearchBody(BaseModel):
    patient: Patient | None = None
    live: bool = False


class CheckBody(BaseModel):
    nct_ids: list[str] | None = None
    top_n: int | None = None
    next_batch: bool = False


class RescoreBody(BaseModel):
    patient: Patient | None = None
    answers: dict[str, str] | None = None
    what_ifs: dict[str, bool] | None = None
    distance_mi: int | None = None
    phases: list[str] | None = None
    recruiting_only: bool | None = None


class SelectBody(BaseModel):
    nct_id: str | None = None
    compare_nct: str | None = None


class ExtractBody(BaseModel):
    note_text: str | None = None
    patient: dict | None = None


class NotesBody(BaseModel):
    nct_id: str | None = None


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "version": __version__,
        "demo_cache": _demo_enabled(),
        "ollama": ollama_reachable(),
        "disclaimer": DISCLAIMER,
    }


@app.get("/api/session")
def get_session() -> dict:
    return view()


@app.get("/api/examples")
def get_examples() -> dict:
    return {"examples": examples(), "disclaimer": DISCLAIMER}


@app.post("/api/examples/{example_id}")
def post_example(example_id: str) -> dict:
    return load_example(example_id)


@app.post("/api/extract")
def post_extract(body: ExtractBody) -> dict:
    return extract(note_text=body.note_text, patient=body.patient, source="note")


@app.post("/api/upload")
async def post_upload(file: UploadFile = File(...)) -> dict:
    suffix = Path(file.filename or "report").suffix.lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".txt", ".html"}:
        raise HTTPException(status_code=400, detail="Upload a PDF, image, or text report.")
    destination = uploads_dir() / f"report{suffix}"
    content = await file.read()
    if len(content) > 12_000_000:
        raise HTTPException(status_code=400, detail="File is larger than 12 MB.")
    destination.write_bytes(content)
    try:
        return extract(report_path=str(destination), source="photo")
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/search")
def post_search(body: SearchBody) -> dict:
    return search(patient=body.patient, force_live=body.live)


@app.post("/api/check")
def post_check(body: CheckBody) -> dict:
    return check(nct_ids=body.nct_ids, top_n=body.top_n, next_batch=body.next_batch)


@app.post("/api/rescore")
def post_rescore(body: RescoreBody) -> dict:
    return rescore(
        patient=body.patient,
        answers=body.answers,
        what_ifs=body.what_ifs,
        distance_mi=body.distance_mi,
        phases=body.phases,
        recruiting_only=body.recruiting_only,
    )


@app.post("/api/select")
def post_select(body: SelectBody) -> dict:
    return select(nct_id=body.nct_id, compare_nct=body.compare_nct)


@app.post("/api/notes")
def post_notes(body: NotesBody) -> dict:
    return draft(nct_id=body.nct_id)


@app.get("/api/events")
async def events() -> StreamingResponse:
    async def stream():
        last = -1
        while True:
            current = load()
            if current.revision != last:
                last = current.revision
                payload = view(current)
                yield f"data: {json.dumps(payload)}\n\n"
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(web_dir() / "index.html")


_static = web_dir()
if _static.exists():
    app.mount("/static", StaticFiles(directory=_static), name="static")
