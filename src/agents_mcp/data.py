"""Fetch agent data-branch files with a TTL cache and a bundled-sample fallback.

Every agent publishes to a `data` branch (see the agents-core README). Files are read
from raw.githubusercontent.com and cached in memory. If an agent's `latest.json` can't
be fetched (branch not created yet, network error, bad JSON), the whole agent switches
to the sample data bundled from agents-hub's test/fixtures/<agent>/ and every response
built from it carries `sample: true`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from importlib import resources
from typing import Any

import httpx

from . import config
from .config import AGENTS, AgentSource

log = logging.getLogger("agents_mcp.data")


class DataUnavailable(Exception):
    """Neither the live data branch nor the bundled sample has the requested file."""


@dataclass(frozen=True)
class Loaded:
    agent: str
    path: str
    data: Any
    sample: bool
    url: str  # where the bytes came from (raw data-branch URL, or the agents-hub fixture)


def _read_sample(agent_id: str, path: str) -> Any:
    ref = resources.files("agents_mcp").joinpath("sample_data", agent_id, *path.split("/"))
    if not ref.is_file():
        raise DataUnavailable(f"{agent_id}/{path} is not in the bundled sample data")
    return json.loads(ref.read_text(encoding="utf-8"))


class DataStore:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        ttl: float | None = None,
        failure_ttl: float | None = None,
        offline: bool | None = None,
        clock=time.monotonic,
    ) -> None:
        self._client = client
        self.ttl = config.CACHE_TTL_SECONDS if ttl is None else ttl
        self.failure_ttl = config.FAILURE_TTL_SECONDS if failure_ttl is None else failure_ttl
        self.offline = config.offline() if offline is None else offline
        self._clock = clock
        self._cache: dict[str, tuple[float, Any]] = {}  # url -> (expires_at, data | Exception)
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=config.HTTP_TIMEOUT_SECONDS,
                headers={"User-Agent": "agents-mcp"},
                follow_redirects=True,
            )
        return self._client

    def clear(self) -> None:
        self._cache.clear()

    async def _fetch_json(self, url: str) -> Any:
        """GET + parse JSON, cached for `ttl` (failures cached for `failure_ttl`)."""
        now = self._clock()
        hit = self._cache.get(url)
        if hit and hit[0] > now:
            if isinstance(hit[1], Exception):
                raise hit[1]
            return hit[1]
        lock = self._locks.setdefault(url, asyncio.Lock())
        async with lock:
            hit = self._cache.get(url)
            if hit and hit[0] > self._clock():
                if isinstance(hit[1], Exception):
                    raise hit[1]
                return hit[1]
            try:
                resp = await self.client.get(url)
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                err = DataUnavailable(f"fetch failed for {url}: {exc}")
                self._cache[url] = (self._clock() + self.failure_ttl, err)
                raise err from exc
            self._cache[url] = (self._clock() + self.ttl, data)
            return data

    async def is_live(self, agent_id: str) -> bool:
        """True when the agent's data branch serves a readable latest.json."""
        if self.offline:
            return False
        try:
            await self._fetch_json(AGENTS[agent_id].raw_url("latest.json"))
        except DataUnavailable as exc:
            log.info("%s: using bundled sample data (%s)", agent_id, exc)
            return False
        return True

    async def get(self, agent_id: str, path: str) -> Loaded:
        """Load one file of an agent's data-branch contract, e.g. ("real_estate",
        "metros/austin-tx.json"). Live when the agent's branch is live, else sample."""
        src: AgentSource = AGENTS[agent_id]
        if await self.is_live(agent_id):
            try:
                data = await self._fetch_json(src.raw_url(path))
                return Loaded(agent_id, path, data, False, src.raw_url(path))
            except DataUnavailable as exc:
                # A live agent missing one optional file: fall back for that file only.
                log.info("%s/%s: live fetch failed, trying sample (%s)", agent_id, path, exc)
        data = _read_sample(agent_id, path)
        return Loaded(agent_id, path, data, True, src.sample_url(path))

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()


_store: DataStore | None = None


def get_store() -> DataStore:
    global _store
    if _store is None:
        _store = DataStore()
    return _store


def set_store(store: DataStore | None) -> None:
    global _store
    _store = store
