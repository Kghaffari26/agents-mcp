"""Where each agent publishes (mirrors agents-hub's config/sources.json)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

RAW_BASE = os.environ.get("AGENTS_MCP_RAW_BASE", "https://raw.githubusercontent.com")
SAMPLE_BASE = "https://github.com/Kghaffari26/agents-hub/blob/main/test/fixtures"
CACHE_TTL_SECONDS = float(os.environ.get("AGENTS_MCP_CACHE_TTL", "600"))
FAILURE_TTL_SECONDS = float(os.environ.get("AGENTS_MCP_FAILURE_TTL", "60"))
HTTP_TIMEOUT_SECONDS = float(os.environ.get("AGENTS_MCP_HTTP_TIMEOUT", "10"))


def offline() -> bool:
    """AGENTS_MCP_OFFLINE=1 serves bundled sample data only (no network)."""
    return os.environ.get("AGENTS_MCP_OFFLINE", "").lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class AgentSource:
    id: str
    name: str
    repo: str
    route: str
    branch: str = "data"
    files: tuple[str, ...] = field(default=("latest.json", "manifest-entry.json"))

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.repo}"

    def raw_url(self, path: str) -> str:
        return f"{RAW_BASE}/{self.repo}/{self.branch}/{path}"

    def sample_url(self, path: str) -> str:
        return f"{SAMPLE_BASE}/{self.id}/{path}"


AGENTS: dict[str, AgentSource] = {
    a.id: a
    for a in (
        AgentSource(
            "real_estate",
            "Real Estate Market Agent",
            "Kghaffari26/real-estate-agent",
            "/real-estate",
        ),
        AgentSource("macro", "Macro & Fed Agent", "Kghaffari26/fed-agent", "/macro"),
        AgentSource(
            "grants",
            "Grants & Contracts Agent",
            "Kghaffari26/sam-agent",
            "/grants",
            files=("latest.json", "manifest-entry.json", "all.json"),
        ),
        AgentSource(
            "repo_maint", "Repo Maintenance Agent", "Kghaffari26/repo-maintain-agent", "/repos"
        ),
    )
}

HUB_URL = "https://github.com/Kghaffari26/agents-hub"
