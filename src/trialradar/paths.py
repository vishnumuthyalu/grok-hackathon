"""Locate the repo, data directory, and demo files no matter the working directory."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").exists() and (candidate / "web").exists():
            return candidate
    return Path.cwd()


def data_dir() -> Path:
    override = os.environ.get("TRIALRADAR_DATA")
    path = Path(override) if override else repo_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def web_dir() -> Path:
    override = os.environ.get("TRIALRADAR_WEB")
    return Path(override) if override else repo_root() / "web"


def demo_cache_path() -> Path:
    override = os.environ.get("DEMO_CACHE_PATH")
    if override:
        return Path(override)
    return repo_root() / "data" / "demo_cache.json"


def session_path() -> Path:
    return data_dir() / "session.json"


def geocode_cache_path() -> Path:
    return data_dir() / "geocode_cache.json"


def uploads_dir() -> Path:
    path = data_dir() / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path
