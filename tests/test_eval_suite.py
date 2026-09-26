"""The eval's questions and scorer, checked without calling the Anthropic API."""

from __future__ import annotations

import asyncio

import pytest
from mcp import Client

from agents_mcp.data import DataStore
from agents_mcp.server import build_server
from evals import run_eval
from evals.scoring import check, load_questions, score, summarize

QUESTIONS = load_questions()


def test_25_questions_cover_every_tool():
    assert len(QUESTIONS) == 25
    assert len({q["id"] for q in QUESTIONS}) == 25
    tools = asyncio.run(run_eval.mcp_tools())
    assert {q["expected_tool"] for q in QUESTIONS} == {t["name"] for t in tools}
    assert run_eval.validate(QUESTIONS, tools) == []


@pytest.mark.parametrize("q", QUESTIONS, ids=[q["id"] for q in QUESTIONS])
async def test_gold_calls_run_against_the_server(q):
    async with Client(build_server(DataStore(offline=True))) as c:
        res = await c.call_tool(q["expected_tool"], q["gold_args"])
    assert not res.is_error, res.content[0].text


def test_scoring_rules():
    assert check({"metro": "new-york-ny"}, True, "NYC")
    assert not check({"metro": "new-york-ny"}, True, "Gotham")
    assert check({"metros": ["tampa-fl", "orlando-fl"]}, True, ["Orlando, FL", "tampa"])
    assert check({"pct": 20}, True, 0.2) and check({"pct": 20}, True, 20)
    assert check({"absent": True}, False, None) and not check({"absent": True}, True, "x")
    assert check({"contains": ["Hot"]}, True, ["Hot", "Warm"])
    assert not check({"contains": ["Hot"]}, True, ["Warm"])
    assert check({"text_any": ["cloud"]}, True, "Cloud migration")
    assert check({"indicator": "unrate"}, True, "UNRATE")
    assert check({"repo": "Kghaffari26/agents-hub"}, True, "agents-hub")
    assert check({"between": [7, 8]}, True, 8) and not check({"between": [7, 8]}, True, 14)
    f = {"filter": {"metric": "median_sale_price", "op": ["<", "<="], "value": 400000}}
    assert check(f, True, [{"metric": "median_sale_price", "op": "<=", "value": 400000}])
    assert not check(f, True, [{"metric": "median_sale_price", "op": ">", "value": 400000}])


def test_score_and_summary():
    q = QUESTIONS[0]
    good = score(q, "get_metro", {"metro": "austin-tx"})
    wrong_arg = score(q, "get_metro", {"metro": "Dallas"})
    wrong_tool = score(q, "find_metros", {})
    assert good["fully_correct"] and good["arg_score"] == 1.0
    assert wrong_arg["tool_correct"] and wrong_arg["arg_score"] == 0.0
    assert not wrong_tool["tool_correct"]
    rows = [{"status": "ok", **s} for s in (good, wrong_arg, wrong_tool)]
    rows.append({"status": "skipped_budget"})
    assert summarize(rows) == {
        "n": 3,
        "tool_accuracy": 0.6667,
        "arg_accuracy": 0.5,
        "fully_correct": 0.3333,
    }


def test_cost_math():
    class U:
        input_tokens, output_tokens = 1_000_000, 1_000_000
        cache_creation_input_tokens, cache_read_input_tokens = 1_000_000, 1_000_000

    assert run_eval.cost("claude-opus-5", U) == pytest.approx(5 + 25 + 6.25 + 0.5)


def test_api_key_fallback(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("AGENTS_ANTHROPIC_API_KEY", "k2")
    assert run_eval.api_key() == "k2"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k1")
    assert run_eval.api_key() == "k1"
