"""MCP server wiring: tools, resources, prompts and the `agents-mcp` CLI."""

from __future__ import annotations

import argparse
import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from . import __version__
from .common import compact
from .config import AGENTS
from .data import DataStore, get_store, set_store
from .tools import grants, macro, overview, real_estate, repos

INSTRUCTIONS = """\
Read-only access to the Agents Hub: four scheduled data agents that publish JSON.
- Housing (50 largest U.S. metros; Redfin, Zillow, FRED, Census): get_metro, compare_metros,
  find_metros, affordability.
- Macro & Fed (FRED indicators, FOMC statements, release calendar): get_indicators,
  get_indicator, get_fomc, upcoming_releases.
- Federal contracts & grants (SAM.gov, Grants.gov, scored for a small software firm):
  search_opportunities, get_opportunity.
- GitHub repo maintenance (health, triage): get_repo_health, get_triage_queue.
- Status of all agents: list_agents.
Rules: every number you state must come from a tool result; do not compute new figures
yourself beyond simple restatement. Cite the `sources` URLs and the `data_through` /
`last_run` dates. If a result has "sample": true, tell the user it is sample data.
Real-estate share metrics are 0-1 ratios (0.0923 = 9.23%)."""

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

TOOLS: list[tuple[Callable[..., Awaitable[dict[str, Any]]], str]] = [
    (overview.list_agents, "List agents and their status"),
    (real_estate.get_metro, "Metro housing snapshot"),
    (real_estate.compare_metros, "Compare metros"),
    (real_estate.find_metros, "Screen metros"),
    (real_estate.affordability, "Mortgage affordability"),
    (macro.get_indicators, "Macro indicators dashboard"),
    (macro.get_indicator, "One macro indicator with history"),
    (macro.get_fomc, "Latest FOMC decision"),
    (macro.upcoming_releases, "Economic calendar"),
    (grants.search_opportunities, "Search contracts & grants"),
    (grants.get_opportunity, "Contract/grant detail"),
    (repos.get_repo_health, "Repo health"),
    (repos.get_triage_queue, "Issue triage queue"),
]


def _as_tool(fn: Callable[..., Awaitable[dict[str, Any]]]):
    """Wrap a tool so its text content is compact JSON and structuredContent the dict."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> CallToolResult:
        payload = await fn(*args, **kwargs)
        return CallToolResult(
            content=[TextContent(type="text", text=compact(payload))],
            structured_content=payload,
        )

    return wrapper


def _register_resources(server: MCPServer) -> None:
    def make(agent_id: str, path: str):
        async def read() -> str:
            loaded = await get_store().get(agent_id, path)
            return compact(
                {"sample": loaded.sample, "source_url": loaded.url, "content": loaded.data}
            )

        return read

    for aid, src in AGENTS.items():
        for path, what in (
            ("latest.json", "latest published output"),
            ("manifest-entry.json", "manifest entry (status, last run, headline)"),
        ):
            server.resource(
                f"agents://{aid}/{path}",
                name=f"{aid}/{path}",
                title=f"{src.name}: {path}",
                description=(
                    f"{src.name} {what}, from {src.repo}@data. Wrapped as "
                    '{"sample", "source_url", "content"}.'
                ),
                mime_type="application/json",
            )(make(aid, path))

    @server.resource(
        "agents://real_estate/metros/{slug}.json",
        name="real_estate/metros/{slug}.json",
        title="Real Estate: metro detail file",
        description="One metro's detail file (36-month series, flags, affordability, brief).",
        mime_type="application/json",
    )
    async def metro_file(slug: str) -> str:
        loaded = await get_store().get("real_estate", f"metros/{slug}.json")
        return compact({"sample": loaded.sample, "source_url": loaded.url, "content": loaded.data})


GROUNDING = (
    "Use only numbers returned by the tools (never estimate or compute new ones), cite the "
    "source URLs and the data_through / last_run dates, and say clearly if any result has "
    "sample: true."
)


def _register_prompts(server: MCPServer) -> None:
    @server.prompt(
        name="weekly_market_brief",
        title="Weekly market brief for a metro",
        description="Write a short weekly housing-market brief for one U.S. metro.",
    )
    def weekly_market_brief(metro: str) -> str:
        return (
            f"Write a weekly housing-market brief for {metro}.\n"
            f"1. Call get_metro with metro={metro!r}.\n"
            f"2. Call affordability with metro={metro!r} (default 20% down, latest rate).\n"
            "3. Call upcoming_releases with days=7 for macro events that could move rates.\n"
            "Then write ~200 words: headline, 3 bullets (prices, inventory/speed, "
            "affordability), what to watch next week, and a Sources line. Compare with the "
            f"national figures in get_metro's `national` block. {GROUNDING}"
        )

    @server.prompt(
        name="economy_this_week",
        title="What changed in the economy this week",
        description="Summarize this week's U.S. macro releases, the Fed, and what's next.",
    )
    def economy_this_week() -> str:
        return (
            "Summarize what changed in the U.S. economy this week.\n"
            "1. Call get_indicators (no group) for the latest readings, regimes and brief.\n"
            "2. Call get_fomc for the latest decision, statement edits and tone.\n"
            "3. Call upcoming_releases with days=7.\n"
            "Write: a one-line takeaway; sections for Inflation, Labor, Growth, Rates & Fed "
            "(only indicators released in the last 7 days, with value, change and period); "
            f"then 'Next week'. {GROUNDING}"
        )

    @server.prompt(
        name="grant_pursuit_shortlist",
        title="Grant pursuit shortlist",
        description="Build a shortlist of contracts/grants worth pursuing, with next steps.",
    )
    def grant_pursuit_shortlist(
        focus: str = "", min_fit: int = 70, closing_within_days: int = 30
    ) -> str:
        q = f", query={focus!r}" if focus else ""
        return (
            "Build a pursuit shortlist of federal contracts and grants.\n"
            f"1. Call search_opportunities with min_fit={min_fit}, "
            f"closing_within_days={closing_within_days}{q}, sort='fit', limit=10.\n"
            "2. For the top 3-5 results call get_opportunity.\n"
            "Output a table (title, agency, fit, recommendation, deadline, days left, link), "
            "then for each shortlisted item: why it fits, risks/red flags, and next steps "
            "from the agent's summary. End with the disclaimer from the data. " + GROUNDING
        )


def build_server(store: DataStore | None = None) -> MCPServer:
    if store is not None:
        set_store(store)
    server = MCPServer(
        name="agents-mcp",
        title="Agents Hub (read-only)",
        version=__version__,
        instructions=INSTRUCTIONS,
        website_url="https://github.com/Kghaffari26/agents-mcp",
    )
    for fn, title in TOOLS:
        server.add_tool(
            _as_tool(fn),
            name=fn.__name__,
            title=title,
            description=fn.__doc__,
            annotations=READ_ONLY,
        )
    _register_resources(server)
    _register_prompts(server)
    return server


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="agents-mcp", description=__doc__)
    p.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    p.add_argument("--host", default="127.0.0.1", help="HTTP bind host (streamable-http)")
    p.add_argument("--port", type=int, default=8000, help="HTTP port (streamable-http)")
    p.add_argument("--path", default="/mcp", help="HTTP endpoint path (streamable-http)")
    p.add_argument("--offline", action="store_true", help="Serve bundled sample data only")
    p.add_argument("--version", action="version", version=f"agents-mcp {__version__}")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    server = build_server(DataStore(offline=True) if args.offline else None)
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run(
            "streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path=args.path,
            stateless_http=True,
            json_response=True,
        )


if __name__ == "__main__":
    main()
