# Decisions

One line per non-obvious choice made while building autonomously.

- Used `mcp` 2.x (`mcp.server.mcpserver.MCPServer`): it is FastMCP renamed in the official SDK's current major; pinning `mcp<2` for the old name would ship on a superseded API.
- Built for Python ≥3.12 (`.python-version` 3.12); the sandbox's default 3.11 was not used.
- Tool functions return dicts; a wrapper returns `CallToolResult` with compact JSON text (the SDK's default text rendering is indent=2) plus the same dict as `structuredContent`.
- Sample fallback is decided per agent by whether `latest.json` is readable live (like agents-hub's "never mix live+sample"); if a live agent is missing one other file, only that file falls back, and the response is still flagged `sample: true`.
- Failed fetches are cached for 60 s (not the full 10 min) so a transient error doesn't pin sample data for long, while a missing branch doesn't cost a 404 on every call.
- Sample citations point at the fixture on GitHub (`agents-hub/blob/main/test/fixtures/...`) so `data_files` is always a real URL.
- Relative-date windows (`closing_within_days`, `upcoming_releases`) use today (UTC) for live data but the snapshot's run date for sample data; otherwise sample windows go empty as days pass.
- `data_through`: real estate = published `data_through`; macro = latest indicator `period` (the agent publishes no single field); grants/repos = run date (they're snapshots of what's open now).
- Resources wrap the file as `{"sample", "source_url", "content"}` so the sample flag reaches resource reads without altering the published JSON itself.
- `affordability` takes `metro` and/or `price` (the $400k/6.5%/30y check is a pure calculation); `down_payment_pct` is a percent, with values in (0,1) read as fractions because models send both.
- `affordability` defaults the rate to the agent's published `rate_now` (Freddie Mac 30-yr), so default outputs reproduce the agent's `payment_now` exactly (tested).
- Payment-to-income, year-ago payment and days-left are computed in code from published inputs; that is deterministic arithmetic, not model output, so it fits "numbers only from the data".
- Metro matching: exact name/slug/city first, then an alias table (NYC, DC, Philly, Vegas…), then rapidfuzz WRatio ≥70; misses raise a ToolError listing the closest metros.
- Repo matching uses a stricter cutoff (80) because repo names share long prefixes (`agents-hub` vs `agents-hub-sandbox`).
- Grant keyword search requires all terms (after dropping stopwords like "services", "contract"), relaxing to any term only when that finds nothing; the response reports `match_mode`.
- `find_metros` filters use the data's native units (0.08 = 8%); the schema says so rather than guessing the user's units.
- `compare_metros` enforces 2–3 metros (the "up to 3" requirement; one metro is `get_metro`).
- `list_agents` reports `stale` only for live data (sample snapshots would always read as stale).
- Streamable HTTP runs stateless with JSON responses: the server holds no per-session state, so this is simplest to host behind anything.
- Eval model: `claude-opus-5` (the current default Opus), `effort: low`, `max_tokens` 1024, `tool_choice: auto`; the tool block is prompt-cached so 25 questions cost ~$0.20.
- Eval scores the first `tool_use` block only; parallel extra calls are recorded but not credited.
- Eval spend cap is enforced pessimistically: each request must fit its worst case (input at cache-write price + full `max_tokens` output) in the remaining budget.
- Session API spend: $0.0564 smoke run (2 questions, not recorded) + $0.2043 full run = $0.2607 of the $0.50 cap.
- Eval questions carry `gold_args`; a unit test runs every gold call against the server and checks it passes its own checks, so the eval can't drift from the tool schemas.
- The live eval workflow uploads results as an artifact and only commits them back when `commit_results` is ticked.
- Pushed to both the session branch `claude/adoring-mccarthy-0ngdkb` and `main` (the user asked for main explicitly).
