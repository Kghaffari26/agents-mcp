# CLAUDE.md — agents-mcp

Read-only MCP server (Python 3.12, uv, official `mcp` SDK 2.x `MCPServer` — the renamed
FastMCP) over the four Agents Hub data branches. User docs: `README.md`.

## Layout

- `src/agents_mcp/server.py` — builds the server: tool registration (`TOOLS` list),
  resources, prompts, `INSTRUCTIONS`, and the `agents-mcp` CLI (`--transport stdio|streamable-http`).
- `src/agents_mcp/data.py` — `DataStore`: raw.githubusercontent fetch, 10-min TTL cache,
  60-s failure cache, per-agent fallback to `sample_data/`. `get_store()`/`set_store()`.
- `src/agents_mcp/common.py` — `envelope()` (every tool response), `fuzzy_pick`, dates.
- `src/agents_mcp/tools/{overview,real_estate,macro,grants,repos}.py` — one async function
  per tool; its docstring is the MCP description, `Annotated[..., Field(description=)]`
  params are the input schema.
- `src/agents_mcp/sample_data/<agent>/` — copied verbatim from agents-hub
  `test/fixtures/<agent>/`. Don't hand-edit; re-copy when the fixtures change.
- `evals/` — tool-selection eval (`questions.json`, `scoring.py`, `run_eval.py`).

## Commands

- `uv sync` · `uv run pytest -q` · `uv run ruff check . && uv run ruff format --check .`
- `uv run agents-mcp --offline` (stdio, sample data) · `--transport streamable-http --port 8000`
- `uv run python -m evals.run_eval --dry-run` (free) · without `--dry-run` it calls the
  Anthropic API: always pass `--max-usd`, and only run it when asked.

## Rules

- **Read-only.** No tool may write anywhere or call anything but HTTP GET on data files.
  Every tool is registered with `READ_ONLY` annotations.
- **Numbers only from the data.** Return published values verbatim. Server-side math is
  limited to deterministic code (amortization, day counts, filter/sort). Never add an LLM
  call to this server.
- **Every response goes through `envelope()`** so it carries `sample`, `data_through`,
  `last_run`, `sources` and `data_files`. Tool errors raise `ToolError` with a hint
  (closest matches, which tool to use instead).
- **Contracts** are the agents-core data-branch README and agents-hub `docs/specs/*` §6.
  Real-estate share metrics are 0–1 ratios; `change_kind` says how to read yoy/mom.
- **Tool descriptions are product surface**: they're what Claude selects on. If you
  change a name, docstring or argument, re-run the eval dry-run and add/adjust questions
  in `evals/questions.json` (each needs `checks` and `gold_args`; tests run the gold call).
- Adding a tool: write it in `tools/`, add it to `server.TOOLS`, add a unit test, add it
  to `tests/test_integration.py::CALLS`, and add at least one eval question.
- Log non-obvious choices as one line in `DECISIONS.md`; keep `STATUS.md` current.
