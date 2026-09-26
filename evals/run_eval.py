"""Tool-selection eval: does Claude, as an MCP client, pick the right tool and arguments?

    uv run --group evals python -m evals.run_eval                # all 25, claude-opus-5
    uv run --group evals python -m evals.run_eval --dry-run      # validate, no API calls
    uv run --group evals python -m evals.run_eval --only re-01,gr-02 --max-usd 0.05

The tool list comes from the real server over an in-process MCP client, so the eval
scores exactly the names, descriptions and schemas that Claude Desktop / Claude Code see.
Each question is a single turn; the first tool_use block is scored. The API key is read
from ANTHROPIC_API_KEY, falling back to AGENTS_ANTHROPIC_API_KEY. Spend is capped
(--max-usd, default 0.50): before every request the worst case (full-price input plus
max_tokens of output) must fit in the remaining budget, or the run stops.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp import Client

from agents_mcp.data import DataStore
from agents_mcp.server import INSTRUCTIONS, build_server
from evals.scoring import load_questions, score, summarize

ROOT = Path(__file__).resolve().parent
# $ per million tokens: (input, output). Cache writes 1.25x input, reads 0.1x input.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
SYSTEM = (
    "You are Claude, connected to the MCP server `agents-mcp`. Use its tools to answer the "
    "user's question with real data.\n\nServer instructions:\n" + INSTRUCTIONS
)


async def mcp_tools() -> list[dict[str, Any]]:
    async with Client(build_server(DataStore(offline=True))) as c:
        tools = (await c.list_tools()).tools
    out = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]
    out[-1]["cache_control"] = {"type": "ephemeral"}  # cache the whole (stable) tool block
    return out


def api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AGENTS_ANTHROPIC_API_KEY")


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def cost(model: str, usage: Any) -> float:
    pin, pout = PRICES[model]
    return (
        usage.input_tokens * pin
        + (usage.cache_creation_input_tokens or 0) * pin * 1.25
        + (usage.cache_read_input_tokens or 0) * pin * 0.1
        + usage.output_tokens * pout
    ) / 1e6


def validate(questions: list[dict[str, Any]], tools: list[dict[str, Any]]) -> list[str]:
    schemas = {t["name"]: t["input_schema"] for t in tools}
    errs = []
    for q in questions:
        if q["expected_tool"] not in schemas:
            errs.append(f"{q['id']}: unknown tool {q['expected_tool']}")
            continue
        props = schemas[q["expected_tool"]].get("properties", {})
        for arg in [*q["checks"], *q.get("gold_args", {})]:
            if arg not in props:
                errs.append(f"{q['id']}: {q['expected_tool']} has no argument {arg!r}")
        s = score(q, q["expected_tool"], q.get("gold_args"))
        if not s["fully_correct"]:
            errs.append(f"{q['id']}: gold_args do not pass the checks: {s['arg_checks']}")
    return errs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument(
        "--model",
        default=os.environ.get("AGENTS_MCP_EVAL_MODEL", "claude-opus-5"),
        choices=sorted(PRICES),
    )
    p.add_argument("--max-usd", type=float, default=0.50)
    p.add_argument("--max-tokens", type=int, default=1024)
    p.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    p.add_argument("--only", help="Comma-separated question ids")
    p.add_argument("--dry-run", action="store_true", help="Validate questions; no API calls")
    p.add_argument("--no-write", action="store_true", help="Don't write results/history")
    args = p.parse_args(argv)

    questions = load_questions()
    if args.only:
        keep = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in keep]
    tools = asyncio.run(mcp_tools())
    errs = validate(questions, tools)
    if errs:
        print("\n".join(errs), file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"OK: {len(questions)} questions valid against {len(tools)} tools.")
        return 0
    key = api_key()
    if not key:
        print("Set ANTHROPIC_API_KEY (or AGENTS_ANTHROPIC_API_KEY).", file=sys.stderr)
        return 2

    import anthropic

    client = anthropic.Anthropic(api_key=key)
    pin, pout = PRICES[args.model]
    longest = max(questions, key=lambda q: len(q["question"]))
    est_in = client.messages.count_tokens(
        model=args.model,
        system=SYSTEM,
        tools=tools,
        messages=[{"role": "user", "content": longest["question"]}],
    ).input_tokens
    worst = (est_in * pin * 1.25 + args.max_tokens * pout) / 1e6
    print(
        f"model={args.model} tool+system tokens~{est_in} worst-case/call=${worst:.4f} "
        f"cap=${args.max_usd:.2f}"
    )

    spent, rows = 0.0, []
    for q in questions:
        if spent + worst > args.max_usd:
            rows.append({"id": q["id"], "status": "skipped_budget"})
            continue
        try:
            resp = client.messages.create(
                model=args.model,
                max_tokens=args.max_tokens,
                system=SYSTEM,
                tools=tools,
                tool_choice={"type": "auto"},
                output_config={"effort": args.effort},
                messages=[{"role": "user", "content": q["question"]}],
            )
        except anthropic.APIStatusError as e:
            rows.append({"id": q["id"], "status": "api_error", "error": f"{e.status_code}"})
            print(f"{q['id']}: API error {e.status_code}", file=sys.stderr)
            continue
        c = cost(args.model, resp.usage)
        spent += c
        calls = [{"name": b.name, "input": b.input} for b in resp.content if b.type == "tool_use"]
        first = calls[0] if calls else {"name": None, "input": {}}
        s = score(q, first["name"], first["input"])
        rows.append(
            {
                "id": q["id"],
                "status": "ok",
                "question": q["question"],
                "expected_tool": q["expected_tool"],
                "tool": first["name"],
                "args": first["input"],
                "all_tool_calls": calls,
                **s,
                "cost_usd": round(c, 5),
                "usage": {
                    "input": resp.usage.input_tokens,
                    "output": resp.usage.output_tokens,
                    "cache_write": resp.usage.cache_creation_input_tokens,
                    "cache_read": resp.usage.cache_read_input_tokens,
                },
            }
        )
        mark = "OK " if s["fully_correct"] else ("ARG" if s["tool_correct"] else "BAD")
        print(f"{mark} {q['id']:7} {first['name']!s:22} {json.dumps(first['input'])[:90]}")

    summary = summarize(rows)
    today = dt.datetime.now(dt.UTC).date().isoformat()
    result = {
        "date": today,
        "git_sha": git_sha(),
        "model": args.model,
        "effort": args.effort,
        "scores": summary,
        "cost_usd": round(spent, 4),
        "max_usd": args.max_usd,
        "questions": len(questions),
        "skipped": sum(r["status"] != "ok" for r in rows),
        "results": rows,
    }
    print(json.dumps({k: result[k] for k in ("model", "scores", "cost_usd", "skipped")}))
    if not args.no_write:
        (ROOT / "results").mkdir(exist_ok=True)
        (ROOT / "results" / f"{today}.json").write_text(json.dumps(result, indent=2) + "\n")
        with (ROOT / "history.jsonl").open("a") as fh:
            fh.write(
                json.dumps(
                    {
                        "date": today,
                        "git_sha": result["git_sha"],
                        "model": args.model,
                        "scores": summary,
                        "cost_usd": result["cost_usd"],
                    }
                )
                + "\n"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
