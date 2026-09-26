from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agents_mcp.tools import overview, repos
from tests.conftest import sample_json

D = sample_json("repo_maint", "latest.json")


async def test_repo_health_overview(sample_store):
    out = await repos.get_repo_health()
    assert len(out["repos"]) == len(D["repos"])
    scores = [r["health"]["score"] for r in out["repos"]]
    assert scores == sorted(scores)
    assert out["headline"] == D["headline"]


async def test_repo_health_detail(sample_store):
    out = await repos.get_repo_health("agents-hub")
    src = next(r for r in D["repos"] if r["full_name"] == "Kghaffari26/agents-hub")
    assert out["repo"]["full_name"] == src["full_name"]
    assert out["repo"]["health"] == src["health"]
    assert out["repo"]["changelog"] == src["changelog"]
    assert all(a["repo"] == src["full_name"] for a in out["repo"]["planned_actions"])


async def test_repo_health_exact_short_name_beats_similar(sample_store):
    out = await repos.get_repo_health("agents-hub-sandbox")
    assert out["repo"]["full_name"] == "Kghaffari26/agents-hub-sandbox"
    with pytest.raises(ToolError):
        await repos.get_repo_health("totally-unknown-repo")


async def test_triage_queue_order_and_filters(sample_store):
    out = await repos.get_triage_queue()
    total = sum(len(r["triage"]) for r in D["repos"])
    assert out["matched"] == total
    pr = [i["priority"] for i in out["issues"]]
    assert pr == sorted(pr)
    bugs = await repos.get_triage_queue(classification=["bug"], priority=["p1", "p2"])
    assert all(
        i["classification"] == "bug" and i["priority"] in {"p1", "p2"} for i in bugs["issues"]
    )
    one = await repos.get_triage_queue(repo="agents-core")
    assert {i["repo"] for i in one["issues"]} == {"Kghaffari26/agents-core"}
    assert any(i["actions"] for i in one["issues"])


async def test_list_agents_sample(sample_store):
    out = await overview.list_agents()
    assert [a["id"] for a in out["agents"]] == ["real_estate", "macro", "grants", "repo_maint"]
    assert out["sample"] is True
    for a in out["agents"]:
        man = sample_json(a["id"], "manifest-entry.json")
        assert a["headline"] == man["headline"]
        assert a["last_run"] == man["last_run_at"]
        assert a["sample"] is True and a["stale"] is None
        assert a["data_through"]


async def test_list_agents_mixed_live(live_mock):
    from agents_mcp.data import DataStore, set_store
    from tests.conftest import serve_samples

    live_mock.get(url__startswith="https://raw.githubusercontent.com/").side_effect = lambda req: (
        serve_samples(req, live={"macro"})
    )
    set_store(DataStore(offline=False))
    out = await overview.list_agents()
    by = {a["id"]: a for a in out["agents"]}
    assert by["macro"]["sample"] is False and isinstance(by["macro"]["stale"], bool)
    assert by["grants"]["sample"] is True
    assert out["sample"] is True
    set_store(None)
