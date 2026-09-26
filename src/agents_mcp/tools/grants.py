"""Grants & contracts tools over Kghaffari26/sam-agent's data branch."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field
from rapidfuzz import fuzz, process

from ..common import (
    agent_sources,
    date_of,
    envelope,
    last_run,
    norm,
    parse_date,
    reference_date,
)
from ..data import DataUnavailable, Loaded, get_store

AGENT = "grants"

Recommendation = Literal["Pursue", "Consider", "Pass"]
SetAside = Literal["total_small_business", "partial_small_business", "any_small_business", "none"]
Sort = Literal["fit", "deadline", "posted"]
_STOP = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "for",
    "of",
    "to",
    "in",
    "on",
    "with",
    "services",
    "service",
    "support",
    "opportunities",
    "opportunity",
    "contracts",
    "contract",
    "grants",
    "grant",
}


async def _load() -> tuple[Loaded, Loaded | None]:
    store = get_store()
    latest = await store.get(AGENT, "latest.json")
    try:
        rows = await store.get(AGENT, "all.json")
    except DataUnavailable:
        rows = None
    return latest, rows


def _rows(latest: Loaded, all_rows: Loaded | None) -> list[dict[str, Any]]:
    if all_rows is not None:
        return list(all_rows.data.get("rows") or [])
    # all.json unavailable: derive rows from top_matches.
    return [
        {
            "id": t["id"],
            "source": t.get("source"),
            "kind": t.get("kind"),
            "type": t.get("notice_type_label"),
            "title": t.get("title"),
            "agency": t.get("agency"),
            "naics": t.get("naics"),
            "set_aside": t.get("set_aside_label"),
            "posted": t.get("posted_date"),
            "deadline": t.get("deadline"),
            "value": (t.get("value") or {}).get("amount"),
            "fit": t.get("fit"),
            "relevance": None,
            "recommendation": t.get("recommendation"),
            "reasons": t.get("reasons"),
            "url": t.get("url"),
            "is_new": t.get("is_new"),
            "in_top": True,
        }
        for t in latest.data.get("top_matches") or []
    ]


def _tokens(q: str) -> list[str]:
    return [t for t in norm(q).split() if t not in _STOP]


def _hay(r: dict[str, Any]) -> str:
    parts = [
        r.get("title"),
        r.get("agency"),
        r.get("type"),
        r.get("id"),
        " ".join(r.get("naics") or []),
        " ".join(r.get("reasons") or []),
        r.get("set_aside"),
    ]
    return " " + norm(" ".join(p for p in parts if p)) + " "


def _set_aside_ok(value: str | None, want: str) -> bool:
    v = (value or "").lower()
    return {
        "none": not v,
        "total_small_business": v.startswith("total small business"),
        "partial_small_business": v.startswith("partial small business"),
        "any_small_business": "small business" in v,
    }[want]


async def search_opportunities(
    query: Annotated[
        str | None,
        Field(
            description=(
                "Keywords matched against title, agency, NAICS, notice type and fit reasons, e.g. "
                "'cloud migration', 'Veterans Affairs', 'data pipeline', '541512'."
            )
        ),
    ] = None,
    min_fit: Annotated[
        int | None,
        Field(
            ge=0,
            le=100,
            description=("Minimum fit score (0-100). Pursue is >=75, Consider >=55 by default."),
        ),
    ] = None,
    closing_within_days: Annotated[
        int | None,
        Field(
            ge=0,
            le=365,
            description=("Only opportunities whose response deadline is within this many days."),
        ),
    ] = None,
    source: Annotated[
        Literal["sam", "grants_gov"] | None,
        Field(description=("'sam' = SAM.gov federal contracts, 'grants_gov' = Grants.gov grants.")),
    ] = None,
    set_aside: Annotated[
        SetAside | None,
        Field(
            description=("Small-business set-aside filter; 'none' = unrestricted (full and open).")
        ),
    ] = None,
    recommendation: Annotated[
        list[Recommendation] | None, Field(description="Keep only these recommendations.")
    ] = None,
    sort: Annotated[
        Sort,
        Field(
            description=(
                "'fit' (best first), 'deadline' (soonest first) or 'posted' (newest first)."
            )
        ),
    ] = "fit",
    limit: Annotated[int, Field(ge=1, le=50, description="Max results.")] = 10,
) -> dict[str, Any]:
    """Search open federal contract opportunities (SAM.gov) and grants (Grants.gov) that the
    grants agent screened for a small software consultancy, with fit score (0-100),
    Pursue/Consider/Pass recommendation, deadline and days left. Filter by keywords, minimum
    fit, deadline window, source, set-aside and recommendation. Use for any 'find/list
    grants, contracts, RFPs, solicitations, bids' question."""
    latest, all_rows = await _load()
    ref = reference_date(latest)
    toks = _tokens(query) if query else []
    match_mode = "all_terms"
    rows = _rows(latest, all_rows)

    def keep(r: dict[str, Any], mode: str) -> bool:
        if min_fit is not None and (r.get("fit") is None or r["fit"] < min_fit):
            return False
        if source and r.get("source") != source:
            return False
        if set_aside and not _set_aside_ok(r.get("set_aside"), set_aside):
            return False
        if recommendation and r.get("recommendation") not in recommendation:
            return False
        dl = parse_date(r.get("deadline"))
        if closing_within_days is not None and (
            dl is None or not 0 <= (dl - ref).days <= closing_within_days
        ):
            return False
        if toks:
            hay = _hay(r)
            hits = [bool(re.search(rf"\b{re.escape(t)}", hay)) for t in toks]
            return all(hits) if mode == "all_terms" else any(hits)
        return True

    hits = [r for r in rows if keep(r, "all_terms")]
    if toks and not hits and len(toks) > 1:
        match_mode = "any_term"
        hits = [r for r in rows if keep(r, "any_term")]

    def sort_key(r: dict[str, Any]):
        if sort == "deadline":
            return (r.get("deadline") or "9999",)
        if sort == "posted":
            return (-(parse_date(r.get("posted")) or ref).toordinal(),)
        return (-(r.get("fit") if r.get("fit") is not None else -1), -(r.get("relevance") or 0))

    hits.sort(key=sort_key)
    out = []
    for r in hits[:limit]:
        dl = parse_date(r.get("deadline"))
        out.append(
            {
                "id": r["id"],
                "title": r.get("title"),
                "agency": r.get("agency"),
                "source": r.get("source"),
                "kind": r.get("kind"),
                "type": r.get("type"),
                "set_aside": r.get("set_aside"),
                "naics": r.get("naics"),
                "posted": r.get("posted"),
                "deadline": r.get("deadline"),
                "days_left": (dl - ref).days if dl else None,
                "value": r.get("value"),
                "fit": r.get("fit"),
                "relevance": r.get("relevance"),
                "recommendation": r.get("recommendation"),
                "reasons": r.get("reasons"),
                "is_new": r.get("is_new"),
                "url": r.get("url"),
            }
        )
    d = latest.data
    files = [latest] + ([all_rows] if all_rows else [])
    return envelope(
        files,
        data_through=date_of(last_run(d)),
        last_run_at=last_run(d),
        sources=[*agent_sources(d), *({"name": r["title"], "url": r["url"]} for r in out)],
        reference_date=ref.isoformat(),
        query={"text": query, "terms": toks, "match_mode": match_mode} if query else None,
        matched=len(hits),
        returned=len(out),
        thresholds=d.get("thresholds"),
        profile=(d.get("profile") or {}).get("name"),
        results=out,
        disclaimer=d.get("disclaimer"),
    )


def _norm_id(s: str) -> str:
    s = s.strip().lower()
    return s.split(":", 1)[1] if ":" in s else s


async def get_opportunity(
    id: Annotated[
        str,
        Field(
            description=(
                "Opportunity id as returned by search_opportunities (e.g. 'sam:174ece4d...'), its "
                "hash without the prefix, a solicitation number, or an exact title."
            )
        ),
    ],
) -> dict[str, Any]:
    """Get one contract/grant opportunity in full: agency, office, NAICS, set-aside,
    deadline, value, fit score with sub-scores, recommendation and confidence, reasons,
    red flags and the agent's pursuit summary (what they want, why it fits, risks, next
    steps). Use after search_opportunities, or when the user names a specific notice."""
    latest, all_rows = await _load()
    ref = reference_date(latest)
    d = latest.data
    want = _norm_id(id)
    top = d.get("top_matches") or []
    rows = _rows(latest, all_rows)

    def matches(r: dict[str, Any]) -> bool:
        return want in {
            _norm_id(r.get("id") or ""),
            (r.get("solicitation_number") or "").lower(),
            norm(r.get("title") or ""),
        } or norm(id) == norm(r.get("title") or "")

    found = next((t for t in top if matches(t)), None)
    level = "full"
    if found is None:
        found = next((r for r in rows if matches(r)), None)
        level = "row"
    if found is None:
        titles = {r["id"]: r.get("title") or "" for r in rows}
        best = process.extractOne(id, titles, scorer=fuzz.WRatio)
        if best and best[1] >= 90:
            key = best[2]
            found = next((t for t in top if t["id"] == key), None)
            level = "full" if found else "row"
            found = found or next(r for r in rows if r["id"] == key)
        else:
            raise ToolError(
                f"No opportunity {id!r}. Use search_opportunities to find ids."
                + (f" Did you mean {best[2]} ({best[0]})?" if best and best[1] >= 60 else "")
            )
    found = dict(found)
    if level == "row":  # all.json rows lack the summary; enrich from top_matches if listed
        found["note"] = "Not in today's top matches, so no written pursuit summary."
    dl = parse_date(found.get("deadline"))
    found["days_left_from_reference"] = (dl - ref).days if dl else None
    files = [latest] + ([all_rows] if all_rows and level == "row" else [])
    return envelope(
        files,
        data_through=date_of(last_run(d)),
        last_run_at=last_run(d),
        sources=[{"name": found.get("title"), "url": found.get("url")}, *agent_sources(d)],
        reference_date=ref.isoformat(),
        detail_level=level,
        opportunity=found,
        profile=d.get("profile"),
        thresholds=d.get("thresholds"),
        disclaimer=d.get("disclaimer"),
    )
