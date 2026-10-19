"""Ollama structured-output provider and the local rules engine.

One trial is one model call. Rescoring uses cached rule metadata and does
not call the model unless a changed fact has no local resolver.
"""

from __future__ import annotations

import json
import os
from typing import Protocol

import httpx

from trialradar.eligibility import evaluate_trial, judge_rule
from trialradar.models import Patient, Rule, TrialCheck

_SCHEMA = {
    "type": "object",
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "status": {"type": "string", "enum": ["met", "not_met", "unknown"]},
                    "reason": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["id", "status", "reason", "evidence"],
            },
        }
    },
    "required": ["rules"],
}


class Provider(Protocol):
    name: str
    calls: int

    def evaluate(self, patient: Patient, criteria_text: str) -> TrialCheck: ...


class StubProvider:
    """Local rules engine. One call evaluates every rule of one trial."""

    name = "stub"
    calls = 0

    def evaluate(self, patient: Patient, criteria_text: str) -> TrialCheck:
        self.calls += 1
        checked = evaluate_trial(patient, criteria_text, provider=self.name)
        return checked


class OllamaProvider:
    name = "ollama"
    calls = 0

    def __init__(self, model: str | None = None, host: str | None = None) -> None:
        self.model = model or os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
        self.host = (host or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")).rstrip("/")

    def evaluate(self, patient: Patient, criteria_text: str) -> TrialCheck:
        self.calls += 1
        local = evaluate_trial(patient, criteria_text, provider="ollama")
        payload = self._complete(patient, local.rules)
        by_id = {item.get("id"): item for item in payload.get("rules") or [] if isinstance(item, dict)}
        merged: list[Rule] = []
        for rule in local.rules:
            item = by_id.get(rule.id)
            if not item:
                merged.append(rule)
                continue
            status = item.get("status")
            if status not in ("met", "not_met", "unknown"):
                status = "unknown"
            evidence = str(item.get("evidence") or "").strip() or "no evidence"
            reason = str(item.get("reason") or "").strip() or rule.reason
            if status == "met" and evidence.lower() in {"no evidence", "none", "n/a", "null"}:
                status = "unknown"
                reason = "No patient evidence was cited, so this stays unknown."
                evidence = "no evidence"
            # Keep local resolver, question, and dependency metadata so later
            # edits re-check only the affected rules without another model call.
            merged.append(rule.model_copy(update={
                "status": status,
                "reason": reason[:500],
                "evidence": evidence[:300],
            }))
        local.rules = merged
        local.provider = "ollama"
        return local

    def _complete(self, patient: Patient, rules: list[Rule]) -> dict:
        slim_rules = [{"id": rule.id, "kind": rule.kind, "text": rule.text} for rule in rules]
        patient_json = patient.model_dump(exclude={"missing_fields"})
        prompt = (
            "You help a treating doctor screen clinical-trial eligibility.\n"
            "Judge every rule. Use ONLY the patient JSON. Do not invent facts, "
            "labs, scans, or biomarkers.\n"
            "status met = the patient passes the rule.\n"
            "For an exclusion, met means the exclusion does not apply.\n"
            "status not_met = the patient fails the rule.\n"
            "status unknown = the patient JSON has no evidence. Use unknown often.\n"
            "evidence must be a short quote copied from the patient JSON, or exactly 'no evidence'.\n"
            "reason is one sentence for the doctor.\n"
            "Return JSON with a rules array covering every id.\n\n"
            f"PATIENT:\n{json.dumps(patient_json, ensure_ascii=True)}\n\n"
            f"RULES:\n{json.dumps(slim_rules, ensure_ascii=True)}"
        )
        response = httpx.post(
            f"{self.host}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "format": _SCHEMA,
                "options": {"temperature": 0},
                "messages": [
                    {"role": "system", "content": "You return only the requested JSON."},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=180,
        )
        response.raise_for_status()
        body = response.json()
        content = (body.get("message") or {}).get("content") or ""
        return _parse_json(content)


def _parse_json(content: str) -> dict:
    content = content.strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(content[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Ollama did not return a JSON object")
    return parsed


def ollama_reachable() -> bool:
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    try:
        response = httpx.get(f"{host}/api/tags", timeout=2)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def vision_extract(image_bytes: bytes, prompt: str) -> str | None:
    """Optional photo path. Returns None when the vision model is not configured or down."""
    model = os.environ.get("OLLAMA_VISION_MODEL", "").strip()
    if not model:
        return None
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    import base64

    try:
        response = httpx.post(
            f"{host}/api/chat",
            json={
                "model": model,
                "stream": False,
                "messages": [{
                    "role": "user",
                    "content": prompt,
                    "images": [base64.b64encode(image_bytes).decode("ascii")],
                }],
            },
            timeout=180,
        )
        response.raise_for_status()
        return ((response.json().get("message") or {}).get("content") or "").strip() or None
    except (httpx.HTTPError, ValueError):
        return None


_provider: Provider | None = None


def set_provider(provider: Provider | None) -> None:
    global _provider
    _provider = provider


def get_provider() -> tuple[Provider, str | None]:
    """Return the active provider and an optional warning if we had to fall back."""
    if _provider is not None:
        return _provider, None
    requested = os.environ.get("LLM_PROVIDER", "ollama").strip().lower()
    if requested == "stub":
        return StubProvider(), None
    if ollama_reachable():
        return OllamaProvider(), None
    fallback = os.environ.get("LLM_FALLBACK", "stub").strip().lower()
    if fallback == "stub":
        return StubProvider(), (
            "Ollama is not running. Rules were checked with the local rules engine. "
            f"Start Ollama and pull {os.environ.get('OLLAMA_MODEL', 'qwen2.5:7b')} to use the model."
        )
    raise RuntimeError("Ollama is not reachable and LLM_FALLBACK is disabled.")


def rejudge_with_provider(provider: Provider, patient: Patient, rules: list[Rule]) -> list[Rule]:
    """One model call for the subset of rules an edit invalidated.

    The stub provider judges locally. Ollama sees only the affected rules.
    """
    if isinstance(provider, StubProvider) or provider.name == "stub":
        return [judge_rule(rule.kind, rule.text, patient, rule.id) for rule in rules]
    text = "\n".join(f"{'Inclusion' if rule.kind == 'inclusion' else 'Exclusion'} Criteria:\n* {rule.text}" for rule in rules)
    checked = provider.evaluate(patient, text)
    by_text = {item.text: item for item in checked.rules}
    merged = []
    for rule in rules:
        replacement = by_text.get(rule.text)
        if replacement is None:
            merged.append(judge_rule(rule.kind, rule.text, patient, rule.id))
        else:
            merged.append(replacement.model_copy(update={"id": rule.id}))
    return merged
