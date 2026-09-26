# Status

_Updated 2026-09-26._

## Done

- 13 read-only tools across the four agents, 8 resources + 1 resource template, 3 prompts.
- stdio and streamable-HTTP (stateless, JSON) transports; `agents-mcp` console script works
  via `uvx --from git+https://github.com/Kghaffari26/agents-mcp agents-mcp`.
- Data layer: raw.githubusercontent fetch, 10-min TTL cache, bundled agents-hub fixtures as
  fallback with `sample: true`.
- Tests: 110 passing (unit tests per tool with mocked HTTP, in-process MCP client
  integration over every tool/resource/prompt, a real streamable-HTTP round trip, eval
  question validation). Ruff clean.
- Tool-selection eval: 25 questions; first run (claude-opus-5, effort low) scored 25/25 on
  tool choice and arguments for $0.20.
- CI: ruff + tests + eval dry-run on push/PR; the live eval on `workflow_dispatch` only.

## Current data state

None of the four agents has a `data` branch yet (checked 2026-09-26), so every response
is sample data from agents-hub's fixtures. When an agent publishes, the server picks it up
within 10 minutes with no code change.

## Next

- Once live data exists, re-run the eval (`Tool-selection eval` workflow) and spot-check
  that live `latest.json` files still match the §6 shapes (the fixtures are the contract).
- Optional: read `history/YYYY-MM-DD.json` snapshots for week-over-week questions
  (raw.githubusercontent can't list directories; would need the contents API).
- Optional: harder eval questions (multi-tool, ambiguous phrasing) now that the basic set
  saturates at 100%.
