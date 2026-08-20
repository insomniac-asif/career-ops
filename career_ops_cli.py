"""Small, dependency-free CLI for the public Career Ops engine."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import career_ops


def _load_leads(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle)
    if isinstance(value, dict):
        value = value.get("leads")
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError("lead file must be a JSON list or an object with leads[]")
    return value


def _emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _plan(leads: list[dict], state_path: str | None = None) -> dict:
    state = (career_ops.load_state(state_path) if state_path
             else career_ops.new_state(enabled=False))
    status = career_ops.build_status(leads, state)
    return {
        "ok": True,
        "selected": status["queue_preview"],
        "eligible_now": status["eligible_now"],
        "daily_cap": status["daily_cap"],
        "authority": status["authority"],
        "boundary": status["boundary"],
    }


def _simulate(leads: list[dict], state_path: str) -> dict:
    career_ops.set_enabled(state_path, True)

    def local_receipt(lead_id: str) -> dict:
        return {
            "ok": True,
            "lead_id": lead_id,
            "draft_label": "synthetic local draft receipt — review before sending",
            "submitted": False,
        }

    result = career_ops.run_preparation(leads, local_receipt, state_path)
    return {
        "ok": result["ok"],
        "prepared_today": result["prepared_today"],
        "remaining_today": result["remaining_today"],
        "results": result.get("results", []),
        "authority": result["authority"],
        "boundary": result["boundary"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="career-ops",
        description="Plan a bounded local career-preparation queue.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="rank synthetic or pre-researched leads")
    plan.add_argument("leads")
    plan.add_argument("--state", help="optional existing state ledger")
    simulate = sub.add_parser(
        "simulate", help="write local synthetic draft receipts; submits nothing")
    simulate.add_argument("leads")
    simulate.add_argument("--state", default=os.path.join(".career-ops", "demo.json"))
    status = sub.add_parser("status", help="inspect a local state ledger")
    status.add_argument("leads")
    status.add_argument("--state", default=os.path.join(".career-ops", "demo.json"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        leads = _load_leads(args.leads)
        if args.command == "plan":
            output = _plan(leads, args.state)
        elif args.command == "simulate":
            output = _simulate(leads, args.state)
        else:
            state = career_ops.load_state(args.state)
            output = career_ops.build_status(leads, state)
        _emit(output)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"career-ops: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
