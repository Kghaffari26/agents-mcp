"""Cross-agent status."""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..common import agent_sources, dedupe_sources, last_run
from ..config import AGENTS
from ..data import DataUnavailable, get_store


def _data_through(agent_id: str, latest: dict[str, Any]) -> str | None:
    if agent_id == "real_estate":
        return latest.get("data_through")
    if agent_id == "macro":
        periods = [i.get("period") for i in latest.get("indicators") or [] if i.get("period")]
        return max(periods) if periods else None
    lr = last_run(latest)
    return lr[:10] if lr else None


async def list_agents() -> dict[str, Any]:
    """List the four Agents Hub data agents (real estate, macro & Fed, grants & contracts,
    repo maintenance) with each one's status, last run time, whether it is stale, next
    scheduled run, headline, key stats, item count and run cost, and which tools cover
    it. Use for 'what can you tell me about', 'what's new', status or overview questions,
    or when unsure which agent has the answer."""
    store = get_store()
    tools = {
        "real_estate": ["get_metro", "compare_metros", "find_metros", "affordability"],
        "macro": ["get_indicators", "get_indicator", "get_fomc", "upcoming_releases"],
        "grants": ["search_opportunities", "get_opportunity"],
        "repo_maint": ["get_repo_health", "get_triage_queue"],
    }
    now = dt.datetime.now(dt.UTC)
    out, sources, files = [], [], []
    for aid, src in AGENTS.items():
        try:
            man = await store.get(aid, "manifest-entry.json")
            lat = await store.get(aid, "latest.json")
        except DataUnavailable as exc:
            out.append({"id": aid, "name": src.name, "status": "unavailable", "error": str(exc)})
            continue
        files += [man.url, lat.url]
        m = man.data
        ran = m.get("last_run_at") or last_run(lat.data)
        stale = None
        if ran and m.get("expected_interval_hours") and not lat.sample:
            age_h = (
                now - dt.datetime.fromisoformat(ran.replace("Z", "+00:00"))
            ).total_seconds() / 3600
            stale = age_h > 2 * m["expected_interval_hours"]
        out.append(
            {
                "id": aid,
                "name": m.get("name", src.name),
                "repo": src.repo_url,
                "sample": man.sample or lat.sample,
                "status": m.get("status"),
                "last_run": ran,
                "last_data_change": m.get("last_data_change_at"),
                "data_through": _data_through(aid, lat.data),
                "stale": stale,
                "expected_interval_hours": m.get("expected_interval_hours"),
                "next_run_hint": m.get("next_run_hint"),
                "headline": m.get("headline"),
                "key_stats": m.get("key_stats"),
                "items_count": m.get("items_count"),
                "run_cost_usd": m.get("run_cost_usd"),
                "warnings": (lat.data.get("meta") or {}).get("warnings") or [],
                "tools": tools[aid],
            }
        )
        sources += agent_sources(lat.data)
    any_sample = any(a.get("sample") for a in out)
    res: dict[str, Any] = {"sample": any_sample}
    if any_sample:
        res["sample_note"] = (
            "Agents marked sample:true have no live data branch yet; their "
            "numbers come from bundled Agents Hub fixtures."
        )
    res["agents"] = out
    res["sources"] = dedupe_sources(sources)
    res["data_files"] = sorted(set(files))
    return res
