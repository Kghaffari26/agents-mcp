from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agents_mcp.tools import real_estate as re_
from tests.conftest import sample_json


def test_monthly_payment_reference_value():
    assert round(re_.monthly_payment(400_000, 6.5, 30), 2) == 2528.27
    assert round(re_.monthly_payment(120_000, 0, 10), 2) == 1000.00


@pytest.mark.parametrize(
    "query,slug",
    [
        ("austin-tx", "austin-tx"),
        ("Austin, TX", "austin-tx"),
        ("austin", "austin-tx"),
        ("NYC", "new-york-ny"),
        ("Philly", "philadelphia-pa"),
        ("St. Louis", "st-louis-mo"),
        ("San Antonio TX", "san-antonio-tx"),
        ("Seatle", "seattle-wa"),
        ("houston", "houston-tx"),
    ],
)
def test_resolve_metro_fuzzy(query, slug):
    idx = sample_json("real_estate", "latest.json")
    assert re_.resolve_metro(idx, query)[0]["slug"] == slug


def test_resolve_metro_unknown_suggests():
    idx = sample_json("real_estate", "latest.json")
    with pytest.raises(ToolError, match="Closest"):
        re_.resolve_metro(idx, "Gotham")


async def test_get_metro_sample(sample_store):
    out = await re_.get_metro("Austin")
    detail = sample_json("real_estate", "metros/austin-tx.json")
    assert out["sample"] is True and "sample_note" in out
    assert out["metro"]["slug"] == "austin-tx"
    assert out["data_through"] == detail["data_through"]
    assert out["last_run"] == "2026-09-25T15:02:44Z"
    msp = detail["latest"]["median_sale_price"]
    assert out["metrics"]["median_sale_price"]["value"] == msp["value"]
    assert out["metrics"]["median_sale_price"]["change_kind"] == "ratio"
    assert out["temperature"]["label"] == detail["temperature"]["label"]
    assert out["affordability"] == detail["affordability"]
    assert "series" not in out
    assert any("redfin" in s["url"] for s in out["sources"])
    assert out["data_files"]


async def test_get_metro_live_series(live_store):
    out = await re_.get_metro("miami-fl", include_series=True)
    assert out["sample"] is False and "sample_note" not in out
    assert len(out["series"]["dates"]) == 36
    assert out["data_files"][0].startswith("https://raw.githubusercontent.com/")


async def test_compare_metros_defaults(sample_store):
    out = await re_.compare_metros(["NYC", "Miami", "Denver"])
    slugs = [m["slug"] for m in out["metros"]]
    assert slugs == ["new-york-ny", "miami-fl", "denver-co"]
    assert list(out["comparison"]) == re_.DEFAULT_COMPARE
    miami = sample_json("real_estate", "metros/miami-fl.json")
    cell = out["comparison"]["median_sale_price"]["values"]["miami-fl"]
    assert cell["value"] == miami["latest"]["median_sale_price"]["value"]
    assert (
        out["comparison"]["payment_to_income"]["values"]["miami-fl"]["value"]
        == miami["affordability"]["payment_to_income"]
    )


async def test_compare_metros_needs_two_distinct(sample_store):
    with pytest.raises(ToolError):
        await re_.compare_metros(["Austin", "austin-tx"])


async def test_find_metros_filters_and_sort(sample_store):
    idx = sample_json("real_estate", "latest.json")
    out = await re_.find_metros(
        temperature=["Hot"],
        filters=[re_.MetricFilter(metric="median_sale_price", op="<", value=400000)],
        sort_by="median_sale_price",
        descending=False,
    )
    expected = sorted(
        (
            m
            for m in idx["metros"]
            if m["temperature"]["label"] == "Hot"
            and m["latest"]["median_sale_price"]["value"] < 400000
        ),
        key=lambda m: m["latest"]["median_sale_price"]["value"],
    )
    assert [r["slug"] for r in out["results"]] == [m["slug"] for m in expected]
    assert out["matched"] == len(expected)


async def test_find_metros_flags_yoy(sample_store):
    out = await re_.find_metros(
        flags=["inventory_surge", "price_decline"],
        filters=[re_.MetricFilter(metric="inventory", field="yoy", op=">=", value=0.3)],
        sort_by="inventory",
        sort_field="yoy",
        limit=3,
    )
    assert out["returned"] <= 3
    vals = [r["sort_value"] for r in out["results"]]
    assert vals == sorted(vals, reverse=True) and all(v >= 0.3 for v in vals)
    assert all({"inventory_surge", "price_decline"} <= set(r["flags"]) for r in out["results"])


async def test_affordability_pure_calc(sample_store):
    out = await re_.affordability(price=500_000, down_payment_pct=20, rate=6.5)
    assert out["inputs"]["loan_amount"] == 400000
    assert out["monthly_payment"] == 2528.27
    assert "metro" not in out


async def test_affordability_metro_defaults_match_agent(sample_store):
    out = await re_.affordability(metro="Austin")
    pub = sample_json("real_estate", "metros/austin-tx.json")["affordability"]
    assert out["monthly_payment"] == pub["payment_now"]
    assert out["year_ago"]["monthly_payment"] == pub["payment_year_ago"]
    assert out["payment_to_income"] == pytest.approx(pub["payment_to_income"], abs=5e-4)
    assert out["inputs"]["rate_pct"] == pub["assumptions"]["rate_now"]


async def test_affordability_fraction_down_payment(sample_store):
    a = await re_.affordability(metro="Denver", down_payment_pct=0.1, rate=7)
    b = await re_.affordability(metro="Denver", down_payment_pct=10, rate=7)
    assert a["monthly_payment"] == b["monthly_payment"]
    assert a["inputs"]["down_payment_pct"] == 10


async def test_affordability_requires_input(sample_store):
    with pytest.raises(ToolError):
        await re_.affordability()
