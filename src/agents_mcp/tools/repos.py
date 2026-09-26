"""Repo-maintenance tools over Kghaffari26/repo-maintain-agent's data branch."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from ..common import agent_sources, date_of, envelope, fuzzy_pick, last_run
from ..data import Loaded, get_store

AGENT = "repo_maint"
Priority = Literal["p0", "p1", "p2", "p3"]
Classification = Literal["bug", "feature", "docs", "question", "chore", "security"]


async def _latest() -> Loaded:
    return await get_store().get(AGENT, "latest.json")


def resolve_repo(d: dict[str, Any], query: str) -> dict[str, Any]:
    repos = {r["full_name"]: r for r in d.get("repos") or []}
    choices = {k: [k.split("/")[-1]] for k in repos}
    key, _ = fuzzy_pick(query, choices, what="watched repo", cutoff=80)
    return repos[key]


def _repo_summary(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "full_name": r["full_name"],
        "url": r.get("url"),
        "role": r.get("role"),
        "health": {
            "score": (r.get("health") or {}).get("score"),
            "grade": (r.get("health") or {}).get("grade"),
        },
        "counts": r.get("counts"),
        "ci_default_branch": r.get("ci_default_branch"),
        "median_first_response_hours": r.get("median_first_response_hours"),
        "days_since_release": r.get("days_since_release"),
        "changelog_ready": bool(r.get("changelog")),
        "suggested_version": (r.get("changelog") or {}).get("suggested_version"),
        "partial": r.get("partial"),
    }


async def get_repo_health(
    repo: Annotated[
        str | None,
        Field(
            description=(
                "A watched repo ('agents-hub' or 'Kghaffari26/agents-hub'). Omit for an overview "
                "of every watched repo."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Get GitHub repository health from the repo-maintenance agent: health score and
    grade with the point deductions, open/untriaged issue and PR counts, stale PRs, CI
    status on the default branch, first-response time, days since release, 12-week
    activity and a drafted changelog with suggested next version. Omit `repo` for all
    watched repos ranked by health."""
    lat = await _latest()
    d = lat.data
    common = {"files": [lat], "data_through": date_of(last_run(d)), "last_run_at": last_run(d)}
    if repo is None:
        repos = sorted(
            (_repo_summary(r) for r in d.get("repos") or []),
            key=lambda r: r["health"]["score"] if r["health"]["score"] is not None else 999,
        )
        return envelope(
            common["files"],
            data_through=common["data_through"],
            last_run_at=common["last_run_at"],
            sources=[
                *agent_sources(d),
                *({"name": r["full_name"], "url": r["url"]} for r in repos),
            ],
            headline=d.get("headline"),
            key_stats=d.get("key_stats"),
            mode=d.get("mode"),
            repos=repos,
        )
    r = resolve_repo(d, repo)
    detail = _repo_summary(r)
    detail["health"] = r.get("health")
    detail["activity_12w"] = r.get("activity_12w")
    detail["stale_prs"] = r.get("stale_prs")
    detail["changelog"] = r.get("changelog")
    detail["untriaged_issues"] = [
        {k: t.get(k) for k in ("number", "title", "url", "classification", "priority")}
        for t in r.get("triage") or []
    ]
    detail["planned_actions"] = [
        a for a in d.get("actions") or [] if a.get("repo") == r["full_name"]
    ]
    return envelope(
        common["files"],
        data_through=common["data_through"],
        last_run_at=common["last_run_at"],
        sources=[{"name": r["full_name"], "url": r.get("url")}, *agent_sources(d)],
        mode=d.get("mode"),
        repo=detail,
    )


async def get_triage_queue(
    repo: Annotated[
        str | None, Field(description=("Limit to one watched repo; omit for every repo."))
    ] = None,
    priority: Annotated[
        list[Priority] | None, Field(description=("Keep only these priorities (p0 = most urgent)."))
    ] = None,
    classification: Annotated[
        list[Classification] | None, Field(description="Keep only these issue types.")
    ] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Max issues.")] = 25,
) -> dict[str, Any]:
    """Get the queue of untriaged GitHub issues the repo-maintenance agent classified:
    per issue its AI classification (bug/feature/docs/question), priority (p0-p3),
    confidence, suggested labels, missing info to ask for, possible duplicates and the
    planned/applied label or comment action, most urgent first. Use for 'what issues need
    attention/triage', 'open bugs', 'what should I work on' questions."""
    lat = await _latest()
    d = lat.data
    repos = [resolve_repo(d, repo)] if repo else list(d.get("repos") or [])
    actions: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for a in d.get("actions") or []:
        actions.setdefault((a.get("repo"), a.get("target")), []).append(
            {k: a.get(k) for k in ("type", "detail", "status", "reason")}
        )
    items = []
    for r in repos:
        for t in r.get("triage") or []:
            if priority and t.get("priority") not in priority:
                continue
            if classification and t.get("classification") not in classification:
                continue
            items.append(
                {
                    "repo": r["full_name"],
                    **t,
                    "actions": actions.get((r["full_name"], t.get("number")), []),
                }
            )
    items.sort(key=lambda t: (t.get("priority") or "p9", t.get("created_at") or ""))
    return envelope(
        [lat],
        data_through=date_of(last_run(d)),
        last_run_at=last_run(d),
        sources=[
            *agent_sources(d),
            *({"name": f"{t['repo']}#{t['number']}", "url": t["url"]} for t in items[:limit]),
        ],
        mode=d.get("mode"),
        matched=len(items),
        returned=min(len(items), limit),
        issues=items[:limit],
    )
