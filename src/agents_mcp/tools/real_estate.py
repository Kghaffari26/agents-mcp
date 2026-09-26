"""Real-estate tools over Kghaffari26/real-estate-agent's data branch."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from ..common import agent_sources, envelope, fuzzy_pick, last_run
from ..data import DataUnavailable, Loaded, get_store

AGENT = "real_estate"

MetricKey = Literal[
    "median_sale_price",
    "homes_sold",
    "new_listings",
    "inventory",
    "months_of_supply",
    "median_dom",
    "avg_sale_to_list",
    "sold_above_list",
    "price_drops",
    "off_market_in_two_weeks",
    "zhvi",
    "zori",
    "permits_total",
    "permits_1unit",
    "permits_5plus",
]
DerivedKey = Literal["temperature_score", "payment_now", "payment_to_income", "homes_sold_12m"]
CompareKey = MetricKey | DerivedKey
TemperatureLabel = Literal["Hot", "Warm", "Balanced", "Cool", "Cold"]
MarketType = Literal["Seller's market", "Balanced", "Buyer's market"]
FlagId = Literal[
    "inventory_surge",
    "inventory_drop",
    "price_decline",
    "price_surge",
    "price_36m_high",
    "price_36m_low",
    "price_cuts_high",
    "slowing",
    "buyers_market",
    "sellers_market",
    "rent_outpacing",
    "permits_boom",
    "permits_bust",
    "payment_jump",
]

UNITS_NOTE = (
    "Share metrics (avg_sale_to_list, sold_above_list, price_drops, off_market_in_two_weeks) "
    "are 0-1 ratios (0.0923 = 9.23%). 'yoy'/'mom' depend on change_kind: 'ratio' = "
    "fractional change (-0.0371 = -3.71%), 'pp' = difference in ratio points (0.0097 = "
    "+0.97 pp), 'diff' = absolute difference in the metric's own unit (days, months)."
)

ALIASES = {
    "nyc": "new-york-ny",
    "new york city": "new-york-ny",
    "manhattan": "new-york-ny",
    "dc": "washington-dc",
    "d c": "washington-dc",
    "la": "los-angeles-ca",
    "philly": "philadelphia-pa",
    "vegas": "las-vegas-nv",
    "twin cities": "minneapolis-mn",
    "dfw": "dallas-tx",
    "the bay area": "oakland-ca",
    "bay area": "oakland-ca",
    "okc": "oklahoma-city-ok",
    "long island": "nassau-county-ny",
    "motor city": "detroit-mi",
    "st louis": "st-louis-mo",
    "saint louis": "st-louis-mo",
    "kc": "kansas-city-mo",
}

DEFAULT_COMPARE: list[str] = [
    "median_sale_price",
    "inventory",
    "median_dom",
    "price_drops",
    "months_of_supply",
    "zori",
    "payment_to_income",
    "temperature_score",
]


# ---------------------------------------------------------------- loading helpers


async def _index() -> Loaded:
    return await get_store().get(AGENT, "latest.json")


def _registry(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["key"]: r for r in index.get("metric_registry") or []}


def resolve_metro(index: dict[str, Any], query: str) -> tuple[dict[str, Any], float]:
    metros = {m["slug"]: m for m in index.get("metros") or []}
    choices = {
        slug: [m["name"], m["name"].split(",")[0], m.get("cbsa") or ""]
        for slug, m in metros.items()
    }
    slug, score = fuzzy_pick(query, choices, aliases=ALIASES, what="metro (of the 50 tracked)")
    return metros[slug], score


async def _metro_file(slug: str) -> Loaded | None:
    try:
        return await get_store().get(AGENT, f"metros/{slug}.json")
    except DataUnavailable:
        return None


def _metric_row(key: str, m: dict[str, Any] | None, reg: dict[str, dict[str, Any]]) -> Any:
    if m is None:
        return None
    r = reg.get(key, {})
    row = {
        "label": r.get("label", key),
        "format": r.get("format"),
        "change_kind": r.get("change_kind"),
    }
    row.update({k: v for k, v in m.items() if k != "delta_format"})
    return row


def _derived(key: str, idx_metro: dict[str, Any], detail: dict[str, Any] | None) -> Any:
    if key == "temperature_score":
        return (idx_metro.get("temperature") or {}).get("score")
    if key == "homes_sold_12m":
        return idx_metro.get("homes_sold_12m")
    aff = (detail or {}).get("affordability") or {}
    return aff.get(key)


def _metric_value(m: dict[str, Any], key: str, field: str) -> float | None:
    if key in DerivedKey.__args__:
        return _derived(key, m, None) if field == "value" else None
    cell = (m.get("latest") or {}).get(key)
    if not cell:
        return None
    if field == "yoy":
        return cell.get("yoy", cell.get("yoy_12m"))
    return cell.get("value")


# ---------------------------------------------------------------- tools


async def get_metro(
    metro: Annotated[
        str,
        Field(
            description=(
                "Metro to look up: a slug ('austin-tx'), a name ('Austin, TX'), or just the city "
                "('Austin', 'NYC'). Fuzzy-matched against the 50 largest U.S. metros."
            )
        ),
    ],
    include_series: Annotated[
        bool,
        Field(
            description=(
                "Also return the monthly 36-month history for every metric. Leave false unless "
                "the user asks about trends over time."
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """Get the latest housing-market snapshot for ONE U.S. metro: median sale price,
    inventory, days on market, price cuts, sale-to-list, months of supply, rents (ZORI),
    home values (ZHVI) and permits, each with YoY/MoM change and 36-month rank; the
    market temperature (Hot/Warm/Balanced/Cool/Cold), buyer's/seller's market type,
    deterministic flags, the published affordability estimate and the agent's written
    brief. Use this for any question about a single metro's housing market."""
    idx = await _index()
    m, score = resolve_metro(idx.data, metro)
    detail = await _metro_file(m["slug"])
    reg = _registry(idx.data)
    files = [idx]
    if detail is not None:
        files.append(detail)
        d = detail.data
        latest = d.get("latest") or {}
        payload: dict[str, Any] = {
            "metro": {"slug": d["slug"], "name": d["name"], "cbsa": d.get("cbsa")},
            "match": {"query": metro, "score": score},
            "temperature": d.get("temperature"),
            "market_type": d.get("market_type"),
            "flags": d.get("flags") or [],
            "metrics": {k: _metric_row(k, v, reg) for k, v in latest.items()},
            "affordability": d.get("affordability"),
            "brief": d.get("brief"),
        }
        data_through = d.get("data_through") or idx.data.get("data_through")
        cites = list((d.get("brief") or {}).get("citations") or [])
        if include_series:
            payload["series"] = d.get("series")
    else:  # metro detail file missing: fall back to the index row
        payload = {
            "metro": {"slug": m["slug"], "name": m["name"], "cbsa": m.get("cbsa")},
            "match": {"query": metro, "score": score},
            "temperature": m.get("temperature"),
            "market_type": m.get("market_type"),
            "flags": m.get("flags") or [],
            "metrics": {k: _metric_row(k, v, reg) for k, v in (m.get("latest") or {}).items()},
            "brief_excerpt": m.get("brief_excerpt"),
            "note": "Metro detail file unavailable; showing the index row only.",
        }
        data_through = idx.data.get("data_through")
        cites = []
    nat = idx.data.get("national") or {}
    payload["national"] = {
        "latest": {
            k: {"value": v.get("value"), "yoy": v.get("yoy")}
            for k, v in (nat.get("latest") or {}).items()
        },
        "mortgage30": (nat.get("rates") or {}).get("latest"),
        "rates_as_of": idx.data.get("rates_as_of"),
    }
    payload["units_note"] = UNITS_NOTE
    return envelope(
        files,
        data_through=data_through,
        last_run_at=last_run(idx.data),
        sources=[*agent_sources(idx.data), *cites],
        **payload,
    )


async def compare_metros(
    metros: Annotated[
        list[str],
        Field(
            min_length=2,
            max_length=3,
            description=("2 or 3 metros to compare side by side (slugs, names or city names)."),
        ),
    ],
    metrics: Annotated[
        list[CompareKey] | None,
        Field(
            description=(
                "Metrics to compare. Omit for a default set (price, inventory, days on market, "
                "price cuts, months of supply, rent, payment-to-income, temperature)."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Compare 2-3 U.S. metros side by side on chosen housing metrics (latest value and
    YoY change for each), plus each metro's temperature and market type. Use this when
    the user names two or three metros and wants them compared."""
    idx = await _index()
    reg = _registry(idx.data)
    picked: list[tuple[dict[str, Any], float]] = []
    for q in metros:
        m, score = resolve_metro(idx.data, q)
        if all(p[0]["slug"] != m["slug"] for p in picked):
            picked.append((m, score))
    if len(picked) < 2:
        raise ToolError("compare_metros needs at least two different metros.")
    keys = list(dict.fromkeys(metrics or DEFAULT_COMPARE))
    files = [idx]
    details: dict[str, dict[str, Any] | None] = {}
    for m, _ in picked:
        f = await _metro_file(m["slug"])
        details[m["slug"]] = f.data if f else None
        if f:
            files.append(f)
    table: dict[str, Any] = {}
    for key in keys:
        values: dict[str, Any] = {}
        for m, _ in picked:
            det = details[m["slug"]]
            if key in DerivedKey.__args__:
                values[m["slug"]] = {"value": _derived(key, m, det)}
            else:
                cell = ((det or m).get("latest") or {}).get(key)
                values[m["slug"]] = (
                    {"value": cell.get("value"), "yoy": cell.get("yoy", cell.get("yoy_12m"))}
                    if cell
                    else None
                )
        r = reg.get(key, {})
        table[key] = {
            "label": r.get("label", key.replace("_", " ")),
            "format": r.get("format", "ratio" if key == "payment_to_income" else None),
            "change_kind": r.get("change_kind"),
            "values": values,
        }
    return envelope(
        files,
        data_through=idx.data.get("data_through"),
        last_run_at=last_run(idx.data),
        sources=agent_sources(idx.data),
        metros=[
            {
                "slug": m["slug"],
                "name": m["name"],
                "match_score": s,
                "temperature": m.get("temperature"),
                "market_type": m.get("market_type"),
                "flags": m.get("flags") or [],
            }
            for m, s in picked
        ],
        comparison=table,
        units_note=UNITS_NOTE,
    )


class MetricFilter(BaseModel):
    metric: CompareKey = Field(description="Metric to test.")
    field: Literal["value", "yoy"] = Field(
        "value", description="Test the latest level ('value') or its year-over-year change."
    )
    op: Literal[">", ">=", "<", "<="]
    value: float = Field(
        description=(
            "Threshold in the data's units: shares and 'ratio'-kind YoY are fractions "
            "(0.08 = 8%), prices in dollars, days in days."
        )
    )


_OPS = {">": float.__gt__, ">=": float.__ge__, "<": float.__lt__, "<=": float.__le__}


async def find_metros(
    temperature: Annotated[
        list[TemperatureLabel] | None,
        Field(description="Keep metros whose market temperature label is one of these."),
    ] = None,
    flags: Annotated[
        list[FlagId] | None,
        Field(description="Keep metros carrying ALL of these deterministic flags."),
    ] = None,
    market_type: Annotated[
        list[MarketType] | None,
        Field(description="Keep metros of these market types (from months of supply)."),
    ] = None,
    filters: Annotated[
        list[MetricFilter] | None,
        Field(
            description=(
                "Metric thresholds, all must hold, e.g. [{metric:'median_sale_price', op:'<', "
                "value:400000}, {metric:'inventory', field:'yoy', op:'>=', value:0.2}]."
            )
        ),
    ] = None,
    sort_by: Annotated[CompareKey, Field(description="Metric to rank by.")] = "temperature_score",
    sort_field: Annotated[
        Literal["value", "yoy"], Field(description="Rank by the level or by the YoY change.")
    ] = "value",
    descending: Annotated[bool, Field(description="Highest first (true) or lowest first.")] = True,
    limit: Annotated[int, Field(ge=1, le=50, description="Max metros to return.")] = 10,
) -> dict[str, Any]:
    """Screen and rank the 50 tracked U.S. metros: filter by temperature label (Hot,
    Warm, Balanced, Cool, Cold), flags (e.g. inventory_surge, price_decline), market
    type, and metric thresholds, then sort by any metric. Use this for 'which metros...',
    'where are...', 'top/bottom N metros by...' questions across many metros."""
    idx = await _index()
    reg = _registry(idx.data)
    rows = []
    for m in idx.data.get("metros") or []:
        if temperature and (m.get("temperature") or {}).get("label") not in temperature:
            continue
        if flags and not set(flags) <= set(m.get("flags") or []):
            continue
        if market_type and m.get("market_type") not in market_type:
            continue
        ok = True
        tested: dict[str, Any] = {}
        for f in filters or []:
            v = _metric_value(m, f.metric, f.field)
            tested[f"{f.metric}.{f.field}"] = v
            if v is None or not _OPS[f.op](float(v), float(f.value)):
                ok = False
                break
        if not ok:
            continue
        rows.append((m, tested, _metric_value(m, sort_by, sort_field)))
    with_key = [r for r in rows if r[2] is not None]
    with_key.sort(key=lambda r: r[2], reverse=descending)
    ranked = with_key + [r for r in rows if r[2] is None]
    results = [
        {
            "slug": m["slug"],
            "name": m["name"],
            "temperature": m.get("temperature"),
            "market_type": m.get("market_type"),
            "flags": m.get("flags") or [],
            "sort_value": key_val,
            **({"tested": tested} if tested else {}),
            "median_sale_price": (m.get("latest") or {}).get("median_sale_price"),
        }
        for m, tested, key_val in ranked[:limit]
    ]
    r = reg.get(sort_by, {})
    return envelope(
        [idx],
        data_through=idx.data.get("data_through"),
        last_run_at=last_run(idx.data),
        sources=agent_sources(idx.data),
        matched=len(rows),
        returned=len(results),
        sorted_by={
            "metric": sort_by,
            "field": sort_field,
            "descending": descending,
            "format": r.get("format"),
            "change_kind": r.get("change_kind"),
        },
        results=results,
        units_note=UNITS_NOTE,
    )


def monthly_payment(principal: float, annual_rate_pct: float, term_years: int) -> float:
    """Standard fixed-rate amortization (principal & interest)."""
    n = term_years * 12
    r = annual_rate_pct / 100 / 12
    if r == 0:
        return principal / n
    return principal * r / (1 - (1 + r) ** -n)


async def affordability(
    metro: Annotated[
        str | None,
        Field(
            description=(
                "Metro whose median sale price and median household income to use. Optional if "
                "`price` is given."
            )
        ),
    ] = None,
    down_payment_pct: Annotated[
        float,
        Field(
            ge=0,
            le=100,
            description=(
                "Down payment as a percent of price, e.g. 20 for 20% (a value below 1 such as 0.2 "
                "is read as a fraction)."
            ),
        ),
    ] = 20,
    rate: Annotated[
        float | None,
        Field(
            gt=0,
            le=25,
            description=(
                "Annual mortgage rate in percent, e.g. 6.5. Omit to use the latest published "
                "Freddie Mac 30-year rate from the data."
            ),
        ),
    ] = None,
    term_years: Annotated[int, Field(ge=5, le=40, description="Loan term in years.")] = 30,
    price: Annotated[
        float | None,
        Field(
            gt=0,
            description=(
                "Home price in dollars. Omit to use the metro's latest median sale price."
            ),
        ),
    ] = None,
) -> dict[str, Any]:
    """Calculate the monthly mortgage payment (principal & interest, standard fixed-rate
    amortization) for a metro's median-priced home or a given price, with a chosen down
    payment and optional rate; returns payment-to-income against the metro's median
    household income and the same payment a year ago. Use for 'can I afford', 'monthly
    payment', 'mortgage cost' questions."""
    if metro is None and price is None:
        raise ToolError("Give a metro, a price, or both.")
    dp = (
        down_payment_pct
        if down_payment_pct >= 1 or down_payment_pct == 0
        else (down_payment_pct * 100)
    )
    idx = await _index()
    files = [idx]
    nat_rates = ((idx.data.get("national") or {}).get("rates") or {}).get("latest") or {}
    m = det = None
    match = None
    if metro is not None:
        m, score = resolve_metro(idx.data, metro)
        match = {"query": metro, "score": score}
        f = await _metro_file(m["slug"])
        if f:
            files.append(f)
            det = f.data
    aff = (det or {}).get("affordability") or {}
    assumptions = aff.get("assumptions") or {}

    if price is not None:
        use_price, price_source = float(price), "user"
    else:
        use_price = float(((det or m).get("latest") or {})["median_sale_price"]["value"])
        price_source = "median_sale_price (latest)"
    if rate is not None:
        use_rate, rate_source = float(rate), "user"
    else:
        use_rate = assumptions.get("rate_now") or nat_rates.get("mortgage30")
        if use_rate is None:
            raise ToolError("No published mortgage rate in the data; pass `rate`.")
        use_rate, rate_source = float(use_rate), "Freddie Mac PMMS 30-yr (FRED MORTGAGE30US)"

    down = use_price * dp / 100
    loan = use_price - down
    pay = round(monthly_payment(loan, use_rate, term_years), 2)
    result: dict[str, Any] = {
        "inputs": {
            "price": round(use_price, 2),
            "price_source": price_source,
            "down_payment_pct": dp,
            "down_payment": round(down, 2),
            "loan_amount": round(loan, 2),
            "rate_pct": use_rate,
            "rate_source": rate_source,
            "term_years": term_years,
        },
        "monthly_payment": pay,
        "total_interest": round(pay * term_years * 12 - loan, 2),
        "method": "Fixed-rate amortization P*r/(1-(1+r)^-n), r = rate/12, n = years*12. "
        "Principal & interest only (no taxes, insurance, PMI or HOA).",
    }
    if m is not None:
        result["metro"] = {"slug": m["slug"], "name": m["name"]}
        result["match"] = match
        income = aff.get("median_household_income")
        if income:
            result["median_household_income"] = income
            result["income_year"] = aff.get("income_year")
            result["payment_to_income"] = round(pay * 12 / income, 4)
        py, ry = assumptions.get("price_year_ago"), assumptions.get("rate_year_ago")
        if py and ry and price is None:
            pay_ya = round(monthly_payment(py * (1 - dp / 100), ry, term_years), 2)
            result["year_ago"] = {
                "price": py,
                "rate_pct": ry,
                "monthly_payment": pay_ya,
                "change_pct": round(pay / pay_ya - 1, 4),
            }
        if aff:
            result["published_by_agent"] = aff
    data_through = (det or {}).get("data_through") or idx.data.get("data_through")
    return envelope(
        files,
        data_through=data_through,
        last_run_at=last_run(idx.data),
        sources=agent_sources(idx.data),
        rates_as_of=idx.data.get("rates_as_of"),
        **result,
    )
