import os

import pytest


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIALRADAR_DATA", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("DEMO_CACHE", "0")
    monkeypatch.setenv("LLM_FALLBACK", "stub")
    from trialradar.llm import set_provider

    set_provider(None)
    yield
    set_provider(None)
