from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import httpx
import pytest
import respx

from agents_mcp.config import AGENTS
from agents_mcp.data import DataStore, set_store

SAMPLE = Path(str(resources.files("agents_mcp").joinpath("sample_data")))
REPO_TO_AGENT = {src.repo: aid for aid, src in AGENTS.items()}


def sample_json(agent: str, path: str):
    return json.loads((SAMPLE / agent / path).read_text())


def serve_samples(request: httpx.Request, *, live: set[str] | None = None) -> httpx.Response:
    """Pretend the data branches exist: serve bundled fixtures at their raw URLs."""
    parts = request.url.path.lstrip("/").split("/")
    repo, branch, rel = "/".join(parts[:2]), parts[2], "/".join(parts[3:])
    agent = REPO_TO_AGENT.get(repo)
    f = SAMPLE / (agent or "?") / rel
    if agent is None or branch != "data" or (live is not None and agent not in live):
        return httpx.Response(404, text="404: Not Found")
    if not f.is_file():
        return httpx.Response(404, text="404: Not Found")
    return httpx.Response(
        200, content=f.read_bytes(), headers={"content-type": "text/plain; charset=utf-8"}
    )


@pytest.fixture
def sample_store():
    """No network: every agent falls back to bundled sample data."""
    store = DataStore(offline=True)
    set_store(store)
    yield store
    set_store(None)


@pytest.fixture
def live_mock():
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def live_store(live_mock):
    """All four data branches 'live' (mocked HTTP serving the fixtures)."""
    route = live_mock.get(url__startswith="https://raw.githubusercontent.com/")
    route.side_effect = serve_samples
    store = DataStore(offline=False)
    set_store(store)
    yield store
    set_store(None)
