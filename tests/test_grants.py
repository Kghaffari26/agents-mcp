from __future__ import annotations

import datetime as dt

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agents_mcp.tools import grants
from tests.conftest import sample_json

ROWS = sample_json("grants", "all.json")["rows"]
REF = dt.date(2026, 9, 26)


async def test_search_min_fit_sorted(sample_store):
    out = await grants.search_opportunities(min_fit=80, limit=50)
    exp = [r for r in ROWS if r["fit"] is not None and r["fit"] >= 80]
    assert out["matched"] == len(exp)
    fits = [r["fit"] for r in out["results"]]
    assert fits == sorted(fits, reverse=True)
    assert out["reference_date"] == "2026-09-26"


async def test_search_query_and_source(sample_store):
    out = await grants.search_opportunities(query="cloud modernization")
    assert out["results"][0]["title"] == "Cloud Modernization Support Services"
    g = await grants.search_opportunities(source="grants_gov", limit=50)
    assert g["results"] and {r["source"] for r in g["results"]} == {"grants_gov"}


async def test_search_query_relaxes_to_any_term(sample_store):
    out = await grants.search_opportunities(query="kubernetes cloud")
    assert out["query"]["match_mode"] == "any_term"
    assert out["matched"] > 0


async def test_search_closing_within(sample_store):
    out = await grants.search_opportunities(closing_within_days=14, sort="deadline", limit=50)
    exp = [r for r in ROWS if 0 <= (dt.date.fromisoformat(r["deadline"][:10]) - REF).days <= 14]
    assert out["matched"] == len(exp)
    assert all(0 <= r["days_left"] <= 14 for r in out["results"])
    dls = [r["deadline"] for r in out["results"]]
    assert dls == sorted(dls)


@pytest.mark.parametrize(
    "sa,check",
    [
        ("total_small_business", lambda v: v == "Total Small Business"),
        ("partial_small_business", lambda v: v == "Partial Small Business"),
        ("any_small_business", lambda v: v is not None),
        ("none", lambda v: v is None),
    ],
)
async def test_search_set_aside(sample_store, sa, check):
    out = await grants.search_opportunities(set_aside=sa, limit=50)
    assert out["results"] and all(check(r["set_aside"]) for r in out["results"])


async def test_search_recommendation(sample_store):
    out = await grants.search_opportunities(recommendation=["Pursue"], limit=50)
    assert out["matched"] == sum(r["recommendation"] == "Pursue" for r in ROWS)


async def test_get_opportunity_full(sample_store):
    top = sample_json("grants", "latest.json")["top_matches"][0]
    for key in (top["id"], top["id"].split(":")[1], top["solicitation_number"], top["title"]):
        out = await grants.get_opportunity(key)
        assert out["detail_level"] == "full"
        assert out["opportunity"]["summary"] == top["summary"]
        assert out["opportunity"]["fit"] == top["fit"]


async def test_get_opportunity_row_only(sample_store):
    row = next(r for r in ROWS if not r["in_top"])
    out = await grants.get_opportunity(row["id"])
    assert out["detail_level"] == "row"
    assert out["opportunity"]["title"] == row["title"]


async def test_get_opportunity_missing(sample_store):
    with pytest.raises(ToolError, match="search_opportunities"):
        await grants.get_opportunity("sam:doesnotexist")
