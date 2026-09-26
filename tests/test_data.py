from __future__ import annotations

import httpx
import pytest

from agents_mcp.data import DataStore, DataUnavailable, set_store
from tests.conftest import sample_json, serve_samples

URL = "https://raw.githubusercontent.com/Kghaffari26/fed-agent/data/latest.json"


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


async def test_live_fetch_is_not_sample(live_store):
    got = await live_store.get("macro", "latest.json")
    assert got.sample is False
    assert got.url == URL
    assert got.data["meta"]["agent"] == "macro"


async def test_missing_branch_falls_back_to_sample(live_mock):
    live_mock.get(url__startswith="https://raw.githubusercontent.com/").mock(
        return_value=httpx.Response(404)
    )
    store = DataStore(offline=False)
    got = await store.get("real_estate", "metros/austin-tx.json")
    assert got.sample is True
    assert got.url.startswith("https://github.com/Kghaffari26/agents-hub/blob/main/test/fixtures")
    assert got.data == sample_json("real_estate", "metros/austin-tx.json")


async def test_network_error_falls_back(live_mock):
    live_mock.get(url__startswith="https://raw.githubusercontent.com/").mock(
        side_effect=httpx.ConnectError("boom")
    )
    got = await DataStore(offline=False).get("grants", "latest.json")
    assert got.sample is True


async def test_invalid_json_falls_back(live_mock):
    live_mock.get(url__startswith="https://raw.githubusercontent.com/").mock(
        return_value=httpx.Response(200, text="<html>not json</html>")
    )
    got = await DataStore(offline=False).get("repo_maint", "latest.json")
    assert got.sample is True


async def test_ttl_cache_hits_and_expires(live_mock):
    route = live_mock.get(url__startswith="https://raw.githubusercontent.com/")
    route.side_effect = serve_samples
    clock = Clock()
    store = DataStore(offline=False, ttl=600, clock=clock)
    for _ in range(3):
        await store.get("macro", "latest.json")
    assert route.call_count == 1  # is_live + get share one cached URL
    clock.t += 599
    await store.get("macro", "latest.json")
    assert route.call_count == 1
    clock.t += 2
    await store.get("macro", "latest.json")
    assert route.call_count == 2


async def test_failures_cached_for_failure_ttl(live_mock):
    route = live_mock.get(url__startswith="https://raw.githubusercontent.com/")
    route.mock(return_value=httpx.Response(404))
    clock = Clock()
    store = DataStore(offline=False, failure_ttl=60, clock=clock)
    await store.get("macro", "latest.json")
    await store.get("macro", "latest.json")
    assert route.call_count == 1
    clock.t += 61
    await store.get("macro", "latest.json")
    assert route.call_count == 2


async def test_live_agent_missing_one_file_falls_back_for_that_file(live_mock):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("all.json"):
            return httpx.Response(404)
        return serve_samples(request)

    live_mock.get(url__startswith="https://raw.githubusercontent.com/").side_effect = handler
    store = DataStore(offline=False)
    assert (await store.get("grants", "latest.json")).sample is False
    assert (await store.get("grants", "all.json")).sample is True


async def test_unknown_file_raises(sample_store):
    with pytest.raises(DataUnavailable):
        await sample_store.get("real_estate", "metros/gotham-xx.json")


async def test_offline_never_touches_network(live_mock):
    route = live_mock.get(url__startswith="https://")
    store = DataStore(offline=True)
    set_store(store)
    got = await store.get("macro", "latest.json")
    assert got.sample is True
    assert route.call_count == 0
    set_store(None)
