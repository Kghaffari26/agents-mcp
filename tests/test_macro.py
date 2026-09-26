from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agents_mcp.tools import macro
from tests.conftest import sample_json


async def test_get_indicators_all(sample_store):
    out = await macro.get_indicators()
    d = sample_json("macro", "latest.json")
    assert len(out["indicators"]) == len(d["indicators"])
    assert out["regimes"] == d["regimes"]
    assert out["headline"] == d["headline"]
    assert "series" not in out["indicators"][0]
    assert out["data_through"] == max(i["period"] for i in d["indicators"])
    assert any(s["url"] == "https://fred.stlouisfed.org/series/CPIAUCSL" for s in out["sources"])


async def test_get_indicators_group(sample_store):
    out = await macro.get_indicators(group="inflation")
    assert {i["group"] for i in out["indicators"]} == {"inflation"}
    assert "inflation" in out["regime"]
    assert "headline" not in out


@pytest.mark.parametrize(
    "q,id_",
    [
        ("cpi", "cpi"),
        ("CPIAUCSL", "cpi"),
        ("unemployment", "unrate"),
        ("Core PCE", "core_pce"),
        ("jobs report", "payrolls"),
        ("10-year Treasury", "t10y"),
    ],
)
def test_resolve_indicator(q, id_):
    assert macro.resolve_indicator(sample_json("macro", "latest.json"), q)["id"] == id_


async def test_get_indicator_range(sample_store):
    out = await macro.get_indicator("cpi", range="1y")
    cpi = next(i for i in sample_json("macro", "latest.json")["indicators"] if i["id"] == "cpi")
    assert out["indicator"]["primary"]["value"] == cpi["primary"]["value"]
    h = out["history"]
    assert h["values"][-1] == cpi["series"]["values"][-1]
    assert 12 <= h["points"] <= 13
    full = await macro.get_indicator("cpi", range="all")
    assert full["history"]["points"] == len(cpi["series"]["dates"])


async def test_get_indicator_custom_dates(sample_store):
    out = await macro.get_indicator("unrate", start="2025-01-01", end="2025-06-30")
    assert out["history"]["dates"][0] == "2025-01-01"
    assert out["history"]["dates"][-1] == "2025-06-01"
    with pytest.raises(ToolError):
        await macro.get_indicator("unrate", start="Jan 2025")


async def test_get_fomc(sample_store):
    out = await macro.get_fomc()
    f = sample_json("macro", "latest.json")["fomc"]
    assert out["decision"]["decision"] == f["latest"]["decision"]
    assert out["decision"]["change_bp"] == f["latest"]["change_bp"]
    assert "latest_text" not in out["decision"]
    assert out["statement_changes"] == f["latest"]["changes"]
    assert out["ai_read"]["tone_shift"] == f["latest"]["read"]["tone_shift"]
    assert out["data_through"] == f["latest"]["date"]
    assert out["sources"][0]["url"] == f["latest"]["url"]
    full = await macro.get_fomc(include_statement_text=True)
    assert full["decision"]["latest_text"] == f["latest"]["latest_text"]


async def test_upcoming_releases_sample_uses_snapshot_date(sample_store):
    out = await macro.upcoming_releases(days=7)
    assert out["window"]["from"] == "2026-09-25"
    dates = [r["date"] for r in out["releases"]]
    assert dates and all("2026-09-25" <= d <= "2026-10-02" for d in dates)
    assert out["fomc_meeting"] is None
    longer = await macro.upcoming_releases(days=40)
    assert longer["fomc_meeting"]["start"] == "2026-10-27"


async def test_upcoming_releases_live_uses_today(live_store, monkeypatch):
    import datetime as dt

    from agents_mcp import common

    monkeypatch.setattr(common, "today_utc", lambda: dt.date(2026, 10, 1))
    out = await macro.upcoming_releases(days=1)
    assert out["sample"] is False
    assert {r["date"] for r in out["releases"]} == {"2026-10-01", "2026-10-02"}
