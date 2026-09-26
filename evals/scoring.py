"""Scoring for the tool-selection eval (no API calls; unit-tested)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_mcp.tools.macro import resolve_indicator
from agents_mcp.tools.real_estate import resolve_metro
from agents_mcp.tools.repos import resolve_repo

QUESTIONS = Path(__file__).with_name("questions.json")
_SAMPLE = Path(__file__).resolve().parents[1] / "src" / "agents_mcp" / "sample_data"


def load_questions() -> list[dict[str, Any]]:
    return json.loads(QUESTIONS.read_text())


def _sample(agent: str) -> dict[str, Any]:
    return json.loads((_SAMPLE / agent / "latest.json").read_text())


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


def check(rule: dict[str, Any], present: bool, value: Any) -> bool:
    """Apply one arg rule. `present` is False when the arg was omitted (or null)."""
    kind, exp = next(iter(rule.items()))
    if kind == "absent":
        return not present
    if not present:
        return False
    if kind == "equals":
        if isinstance(exp, str) and isinstance(value, str):
            return value.strip().lower() == exp.lower()
        return value == exp
    if kind == "number":
        return isinstance(value, int | float) and abs(value - exp) <= 1e-6 * max(1, abs(exp))
    if kind == "pct":  # 20 or 0.2 both mean 20%
        return isinstance(value, int | float) and (
            abs(value - exp) < 1e-9 or abs(value * 100 - exp) < 1e-6
        )
    if kind == "between":
        return isinstance(value, int | float) and exp[0] <= value <= exp[1]
    if kind == "prefix":
        return isinstance(value, str) and value.startswith(exp)
    if kind == "contains":
        if not isinstance(value, list):
            value = [value]
        got = {str(v).lower() for v in value}
        return all(str(e).lower() in got for e in exp)
    if kind == "text_any":
        return isinstance(value, str) and any(t.lower() in value.lower() for t in exp)
    if kind == "metro":
        m = _safe(resolve_metro, _sample("real_estate"), value) if isinstance(value, str) else None
        return bool(m) and m[0]["slug"] == exp
    if kind == "metros":
        if not isinstance(value, list):
            return False
        idx = _sample("real_estate")
        got = [_safe(resolve_metro, idx, v) for v in value]
        return None not in got and {g[0]["slug"] for g in got} == set(exp)
    if kind == "indicator":
        i = _safe(resolve_indicator, _sample("macro"), value) if isinstance(value, str) else None
        return bool(i) and i["id"] == exp
    if kind == "repo":
        r = _safe(resolve_repo, _sample("repo_maint"), value) if isinstance(value, str) else None
        return bool(r) and r["full_name"] == exp
    if kind == "filter":
        ops = exp["op"] if isinstance(exp["op"], list) else [exp["op"]]
        return isinstance(value, list) and any(
            isinstance(f, dict)
            and f.get("metric") == exp["metric"]
            and f.get("op") in ops
            and f.get("field", "value") == "value"
            and f.get("value") == exp["value"]
            for f in value
        )
    raise ValueError(f"unknown rule {kind}")


def score(q: dict[str, Any], tool: str | None, args: dict[str, Any] | None) -> dict[str, Any]:
    args = args or {}
    tool_ok = tool == q["expected_tool"]
    results = {}
    for name, rule in q["checks"].items():
        present = name in args and args[name] is not None
        results[name] = tool_ok and check(rule, present, args.get(name))
    n = len(results)
    arg_score = (sum(results.values()) / n if n else 1.0) if tool_ok else 0.0
    return {
        "tool_correct": tool_ok,
        "arg_checks": results,
        "arg_score": round(arg_score, 4),
        "fully_correct": tool_ok and all(results.values()),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if r.get("status") == "ok"]
    n = len(scored)
    if not n:
        return {"n": 0}
    tool = [r for r in scored if r["tool_correct"]]
    return {
        "n": n,
        "tool_accuracy": round(len(tool) / n, 4),
        "arg_accuracy": round(sum(r["arg_score"] for r in tool) / len(tool), 4) if tool else 0.0,
        "fully_correct": round(sum(r["fully_correct"] for r in scored) / n, 4),
    }
