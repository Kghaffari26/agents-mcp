"""Shared response envelope, errors and matching helpers for every tool."""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Iterable
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from rapidfuzz import fuzz, process

from .data import Loaded

SAMPLE_NOTE = (
    "SAMPLE DATA: this agent has not published a live data branch yet (or it could not be "
    "reached), so these numbers come from the bundled Agents Hub test fixtures. Say so "
    "when you use them."
)


def compact(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _source(s: dict[str, Any]) -> dict[str, Any]:
    out = {"name": s.get("name"), "url": s.get("url")}
    if s.get("retrieved_at"):
        out["retrieved_at"] = s["retrieved_at"]
    return out


def dedupe_sources(sources: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for s in sources:
        if not s or not s.get("url"):
            continue
        key = s["url"]
        if key not in seen:
            seen[key] = _source(s)
        elif s.get("retrieved_at") and "retrieved_at" not in seen[key]:
            seen[key]["retrieved_at"] = s["retrieved_at"]
    return list(seen.values())


def date_of(ts: str | None) -> str | None:
    return ts[:10] if ts else None


def last_run(latest: dict[str, Any]) -> str | None:
    meta = latest.get("meta") or {}
    return meta.get("finished_at") or meta.get("started_at")


def agent_sources(latest: dict[str, Any]) -> list[dict[str, Any]]:
    meta = latest.get("meta") or {}
    return dedupe_sources([*(meta.get("sources") or []), *(latest.get("sources") or [])])


def envelope(
    files: Iterable[Loaded],
    *,
    data_through: str | None,
    last_run_at: str | None,
    sources: Iterable[dict[str, Any]] = (),
    **payload: Any,
) -> dict[str, Any]:
    """Standard response: provenance first, then the payload, then citations."""
    files = list(files)
    agents = sorted({f.agent for f in files})
    sample = any(f.sample for f in files)
    out: dict[str, Any] = {
        "agent": agents[0] if len(agents) == 1 else agents,
        "sample": sample,
        "data_through": data_through,
        "last_run": last_run_at,
    }
    if sample:
        out["sample_note"] = SAMPLE_NOTE
    out.update(payload)
    out["sources"] = dedupe_sources(sources)
    out["data_files"] = sorted({f.url for f in files})
    return out


def today_utc() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


def reference_date(latest: Loaded) -> dt.date:
    """'Today' for relative-date filters. Live data: today (UTC). Sample data is a frozen
    snapshot, so use the day it was generated; otherwise every window would be empty."""
    if latest.sample:
        ran = date_of(last_run(latest.data))
        if ran:
            return dt.date.fromisoformat(ran)
    return today_utc()


def parse_date(s: str | None) -> dt.date | None:
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


_norm_re = re.compile(r"[^a-z0-9]+")


def norm(s: str) -> str:
    return _norm_re.sub(" ", s.lower()).strip()


def fuzzy_pick(
    query: str,
    choices: dict[str, list[str]],
    *,
    aliases: dict[str, str] | None = None,
    what: str,
    cutoff: float = 70,
) -> tuple[str, float]:
    """Resolve `query` to a key of `choices` (key -> searchable strings).

    Exact (normalized) matches win, then aliases, then rapidfuzz WRatio. Raises a
    ToolError listing the closest options when nothing clears `cutoff`.
    """
    q = norm(query)
    if not q:
        raise ToolError(f"Empty {what}.")
    for key, names in choices.items():
        if q == norm(key) or any(q == norm(n) for n in names):
            return key, 100.0
    if aliases and q in aliases:
        return aliases[q], 100.0
    flat: dict[str, str] = {}
    for key, names in choices.items():
        for n in [key, *names]:
            flat[f"{key}\x00{n}"] = norm(n)
    best = process.extract(q, flat, scorer=fuzz.WRatio, limit=8)
    if best and best[0][1] >= cutoff:
        return best[0][2].split("\x00")[0], round(best[0][1], 1)
    suggestions: list[str] = []
    for _, _, k in best:
        key = k.split("\x00")[0]
        if key not in suggestions:
            suggestions.append(key)
    raise ToolError(
        f"No {what} matches {query!r}. Closest: {', '.join(suggestions[:5]) or 'none'}."
    )
