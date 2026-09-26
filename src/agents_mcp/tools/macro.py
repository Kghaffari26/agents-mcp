"""Macro & Fed tools over Kghaffari26/fed-agent's data branch."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from ..common import agent_sources, envelope, fuzzy_pick, last_run, parse_date, reference_date
from ..data import Loaded, get_store

AGENT = "macro"

Group = Literal["inflation", "labor", "growth", "rates", "sentiment"]
Range = Literal["3m", "6m", "1y", "2y", "5y", "10y", "all"]
_RANGE_DAYS = {"3m": 92, "6m": 183, "1y": 366, "2y": 731, "5y": 1827, "10y": 3653}

INDICATOR_ALIASES = {
    "inflation": "cpi",
    "consumer prices": "cpi",
    "headline inflation": "cpi",
    "core inflation": "core_cpi",
    "pce inflation": "pce",
    "jobs": "payrolls",
    "nfp": "payrolls",
    "jobs report": "payrolls",
    "unemployment": "unrate",
    "jobless rate": "unrate",
    "jobless claims": "claims",
    "initial claims": "claims",
    "job openings": "jolts",
    "wages": "ahe",
    "wage growth": "ahe",
    "gdp": "gdp",
    "fed funds": "fed_funds_upper",
    "fed funds rate": "fed_funds_upper",
    "policy rate": "fed_funds_upper",
    "10 year": "t10y",
    "10y": "t10y",
    "2 year": "t2y",
    "2y": "t2y",
    "30 year treasury": "t30y",
    "yield curve": "spread_10y2y",
    "mortgage rate": "mortgage30",
    "mortgage rates": "mortgage30",
    "consumer sentiment": "umich",
    "sentiment": "umich",
    "breakeven": "breakeven5y",
}

_SUMMARY_KEYS = (
    "id",
    "name",
    "group",
    "fred_series",
    "frequency",
    "units_display",
    "primary",
    "change",
    "secondary",
    "period",
    "period_label",
    "released_at",
    "next_release",
    "delayed",
    "revision",
    "source_url",
)


async def _latest() -> Loaded:
    return await get_store().get(AGENT, "latest.json")


def _summary(ind: dict[str, Any]) -> dict[str, Any]:
    return {k: ind.get(k) for k in _SUMMARY_KEYS if k in ind}


def _max_period(inds: list[dict[str, Any]]) -> str | None:
    periods = [i.get("period") for i in inds if i.get("period")]
    return max(periods) if periods else None


def _ind_source(ind: dict[str, Any]) -> dict[str, Any]:
    return {"name": f"FRED: {ind.get('fred_series')}", "url": ind.get("source_url")}


def resolve_indicator(latest: dict[str, Any], query: str) -> dict[str, Any]:
    inds = {i["id"]: i for i in latest.get("indicators") or []}
    choices = {k: [i.get("name") or "", i.get("fred_series") or ""] for k, i in inds.items()}
    key, _ = fuzzy_pick(query, choices, aliases=INDICATOR_ALIASES, what="indicator")
    return inds[key]


async def get_indicators(
    group: Annotated[
        Group | None,
        Field(
            description=(
                "Only this group: inflation (CPI, PCE, breakevens), labor (unemployment, payrolls, "
                "claims, JOLTS, wages), growth (GDP, retail sales, industrial production), rates "
                "(fed funds, Treasuries, spreads, mortgage rate) or sentiment. Omit for all."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Get the latest reading of every tracked U.S. macro indicator (or one group): value,
    change vs prior, period, release date, next release, revisions and delays, plus the
    agent's regime labels (inflation, labor, growth, policy, curve) and weekly brief
    bullets. Use for 'how is the economy', dashboard-style or multi-indicator questions."""
    lat = await _latest()
    d = lat.data
    inds = [i for i in d.get("indicators") or [] if group is None or i.get("group") == group]
    regimes = d.get("regimes") or {}
    payload: dict[str, Any] = {}
    if group is None:
        payload["headline"] = d.get("headline")
        payload["key_stats"] = d.get("key_stats")
        payload["regimes"] = regimes
        brief = d.get("brief") or {}
        payload["brief"] = {
            "bullets": [
                {"text": b.get("text"), "citations": b.get("citations")}
                for b in brief.get("bullets") or []
            ],
            "narrative_source": brief.get("narrative_source"),
            "generated_at": brief.get("generated_at"),
        }
    else:
        rk = {"rates": "policy"}.get(group, group)
        if rk in regimes:
            payload["regime"] = {rk: regimes[rk]}
    payload["indicators"] = [_summary(i) for i in inds]
    cites = [_ind_source(i) for i in inds]
    if group is None:
        for b in (d.get("brief") or {}).get("bullets") or []:
            cites.extend(b.get("citations") or [])
    return envelope(
        [lat],
        data_through=_max_period(inds),
        last_run_at=last_run(d),
        sources=[*agent_sources(d), *cites],
        **payload,
    )


async def get_indicator(
    indicator: Annotated[
        str,
        Field(
            description=(
                "Indicator id (cpi, core_cpi, pce, core_pce, breakeven5y, unrate, payrolls, "
                "claims, jolts, ahe, gdp, retail, indpro, fed_funds_upper, effr, t3m, t2y, t5y, "
                "t10y, t30y, spread_10y2y, spread_10y3m, mortgage30, umich), a FRED series id "
                "(e.g. CPIAUCSL) or a plain name ('unemployment rate')."
            )
        ),
    ],
    range: Annotated[
        Range,
        Field(
            description=("How much history to return, counted back from the latest observation.")
        ),
    ] = "1y",
    start: Annotated[
        str | None, Field(description=("Optional start date YYYY-MM-DD; overrides `range`."))
    ] = None,
    end: Annotated[str | None, Field(description="Optional end date YYYY-MM-DD.")] = None,
) -> dict[str, Any]:
    """Get ONE macro indicator in depth: latest reading and change, revision, release
    dates, and its history (the published primary measure, e.g. CPI YoY %) over a chosen
    range. Use when the user asks about a specific indicator's level or trend over time."""
    lat = await _latest()
    ind = resolve_indicator(lat.data, indicator)
    series = ind.get("series") or {}
    dates, values = list(series.get("dates") or []), list(series.get("values") or [])
    last = parse_date(dates[-1]) if dates else None
    lo = parse_date(start)
    if start and lo is None:
        raise ToolError(f"Bad start date {start!r}; use YYYY-MM-DD.")
    hi = parse_date(end)
    if end and hi is None:
        raise ToolError(f"Bad end date {end!r}; use YYYY-MM-DD.")
    if lo is None and range != "all" and last is not None:
        lo = last - dt.timedelta(days=_RANGE_DAYS[range])
    pts = [
        (d, v)
        for d, v in zip(dates, values, strict=False)
        if (lo is None or parse_date(d) >= lo) and (hi is None or parse_date(d) <= hi)
    ]
    return envelope(
        [lat],
        data_through=ind.get("period"),
        last_run_at=last_run(lat.data),
        sources=[_ind_source(ind), *agent_sources(lat.data)],
        indicator=_summary(ind),
        history={
            "measure": (ind.get("primary") or {}).get("label"),
            "units": ind.get("units_display"),
            "range": "custom" if start or end else range,
            "points": len(pts),
            "dates": [p[0] for p in pts],
            "values": [p[1] for p in pts],
            "available_from": dates[0] if dates else None,
        },
    )


async def get_fomc(
    include_statement_text: Annotated[
        bool,
        Field(
            description=("Also return the full text of the latest and previous FOMC statements.")
        ),
    ] = False,
) -> dict[str, Any]:
    """Get the latest Federal Reserve FOMC decision: date, hike/cut/hold, target range,
    size in bp, votes and dissents; the sentence-by-sentence edits versus the previous
    statement; the agent's AI read of the statement (summary, hawkish/dovish tone shift,
    key phrases); the next meeting; and the latest minutes summary. Use for any question
    about the Fed's rate decision or statement."""
    lat = await _latest()
    f = lat.data.get("fomc") or {}
    latest = dict(f.get("latest") or {})
    if not include_statement_text:
        latest.pop("latest_text", None)
        latest.pop("previous_text", None)
    read = latest.pop("read", None)
    changes = latest.pop("changes", [])
    sources = [{"name": "Federal Reserve: FOMC statement", "url": latest.get("url")}]
    if (f.get("minutes") or {}).get("url"):
        sources.append({"name": "Federal Reserve: FOMC minutes", "url": f["minutes"]["url"]})
    return envelope(
        [lat],
        data_through=latest.get("date"),
        last_run_at=last_run(lat.data),
        sources=[*sources, *agent_sources(lat.data)],
        decision=latest,
        statement_changes=changes,
        ai_read=read,
        next_meeting=f.get("next_meeting"),
        minutes=f.get("minutes"),
        policy_regime=(lat.data.get("regimes") or {}).get("policy"),
    )


async def upcoming_releases(
    days: Annotated[
        int, Field(ge=1, le=90, description=("Look this many days ahead (inclusive)."))
    ] = 14,
) -> dict[str, Any]:
    """List scheduled U.S. economic data releases (CPI, jobs report, PCE, claims, JOLTS,
    sentiment, Treasury rates...) and the next FOMC meeting within the next N days, with
    each affected indicator's current reading. Use for 'what's coming up', 'when is the
    next jobs report/CPI' or economic-calendar questions."""
    lat = await _latest()
    d = lat.data
    ref = reference_date(lat)
    until = ref + dt.timedelta(days=days)
    inds = {i["id"]: i for i in d.get("indicators") or []}
    events = []
    for c in d.get("calendar") or []:
        cd = parse_date(c.get("date"))
        if cd is None or not (ref <= cd <= until):
            continue
        events.append(
            {
                "date": c["date"],
                "release": c.get("release"),
                "indicators": [
                    {
                        "id": i,
                        "name": inds[i].get("name"),
                        "current": inds[i].get("primary"),
                        "current_period": inds[i].get("period_label"),
                    }
                    if i in inds
                    else {"id": i}
                    for i in c.get("indicator_ids") or []
                ],
            }
        )
    nm = (d.get("fomc") or {}).get("next_meeting") or {}
    nm_start = parse_date(nm.get("start"))
    fomc = nm if nm_start and ref <= nm_start <= until else None
    cal_dates = [c.get("date") for c in d.get("calendar") or [] if c.get("date")]
    return envelope(
        [lat],
        data_through=_max_period(list(inds.values())),
        last_run_at=last_run(d),
        sources=agent_sources(d),
        window={"from": ref.isoformat(), "to": until.isoformat(), "days": days},
        releases=events,
        fomc_meeting=fomc,
        calendar_covers={"from": min(cal_dates), "to": max(cal_dates)} if cal_dates else None,
    )
