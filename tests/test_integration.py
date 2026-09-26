"""In-process MCP client: list and call every tool, resource and prompt."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

import pytest
from mcp import Client

from agents_mcp.common import compact
from agents_mcp.data import DataStore
from agents_mcp.server import TOOLS, build_server

CALLS: dict[str, dict] = {
    "list_agents": {},
    "get_metro": {"metro": "Austin"},
    "compare_metros": {"metros": ["Austin", "Denver"], "metrics": ["median_sale_price", "zori"]},
    "find_metros": {"temperature": ["Cold"], "limit": 3},
    "affordability": {"metro": "Phoenix", "down_payment_pct": 10, "rate": 6.5},
    "get_indicators": {"group": "rates"},
    "get_indicator": {"indicator": "cpi", "range": "2y"},
    "get_fomc": {},
    "upcoming_releases": {"days": 14},
    "search_opportunities": {"query": "cloud", "min_fit": 60},
    "get_opportunity": {"id": "sam:174ece4d5472af1e48b258187156fd65"},
    "get_repo_health": {"repo": "agents-core"},
    "get_triage_queue": {"priority": ["p1", "p2"]},
}


def connect() -> Client:
    return Client(build_server(DataStore(offline=True)))


async def test_lists_every_tool_read_only():
    async with connect() as client:
        await _check_lists_every_tool_read_only(client)


async def _check_lists_every_tool_read_only(client):
    tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {fn.__name__ for fn, _ in TOOLS} == set(CALLS)
    for t in tools.values():
        assert t.description and len(t.description) > 80
        assert t.annotations.read_only_hint is True
        assert t.annotations.destructive_hint is False


@pytest.mark.parametrize("name", list(CALLS))
async def test_call_every_tool(name):
    async with connect() as client:
        res = await client.call_tool(name, CALLS[name])
    assert not res.is_error, res.content[0].text
    text = res.content[0].text
    payload = json.loads(text)
    assert text == compact(payload)  # compact JSON
    assert payload == res.structured_content
    assert payload["sample"] is True
    assert payload["sources"], "every response cites sources"
    if name != "list_agents":
        assert payload["data_through"] and payload["last_run"]


async def test_bad_arguments_are_tool_errors():
    async with connect() as client:
        await _check_bad_arguments_are_tool_errors(client)


async def _check_bad_arguments_are_tool_errors(client):
    res = await client.call_tool("compare_metros", {"metros": ["Austin"]})
    assert res.is_error
    res = await client.call_tool("get_metro", {"metro": "Gotham"})
    assert res.is_error and "Closest" in res.content[0].text


async def test_resources():
    async with connect() as client:
        await _check_resources(client)


async def _check_resources(client):
    listed = (await client.list_resources()).resources
    uris = {str(r.uri) for r in listed}
    assert len(uris) == 8
    assert "agents://grants/manifest-entry.json" in uris
    templates = (await client.list_resource_templates()).resource_templates
    assert any("metros/{slug}" in t.uri_template for t in templates)
    for uri in ("agents://macro/latest.json", "agents://real_estate/metros/austin-tx.json"):
        body = json.loads((await client.read_resource(uri)).contents[0].text)
        assert body["sample"] is True and body["content"]


async def test_prompts():
    async with connect() as client:
        await _check_prompts(client)


async def _check_prompts(client):
    names = {p.name for p in (await client.list_prompts()).prompts}
    assert names == {"weekly_market_brief", "economy_this_week", "grant_pursuit_shortlist"}
    p = await client.get_prompt("weekly_market_brief", {"metro": "Tampa"})
    text = p.messages[0].content.text
    assert "get_metro" in text and "Tampa" in text
    p = await client.get_prompt("grant_pursuit_shortlist", {"focus": "data pipeline"})
    assert "search_opportunities" in p.messages[0].content.text
    p = await client.get_prompt("economy_this_week", {})
    assert "get_fomc" in p.messages[0].content.text


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_streamable_http_transport(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agents_mcp.server",
            "--transport",
            "streamable-http",
            "--port",
            str(port),
            "--offline",
        ],
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.2)
        async with Client(f"http://127.0.0.1:{port}/mcp") as c:
            assert len((await c.list_tools()).tools) == 13
            res = await c.call_tool("get_fomc", {})
            assert not res.is_error
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_stdio_entry_point_help():
    out = subprocess.run(
        [sys.executable, "-m", "agents_mcp.server", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--transport" in out.stdout
