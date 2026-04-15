from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import db, engine
from .daily_run import run_daily


def _cmd_init(args: argparse.Namespace) -> None:
    path = Path(args.db)
    db.init_db(path)
    if args.seed:
        db.seed_sample_data(path)
    print(f"Initialized database at {path}")


def _cmd_daily(args: argparse.Namespace) -> None:
    result = run_daily(Path(args.db))
    print(json.dumps(result, indent=2))


def _cmd_add_interaction(args: argparse.Namespace) -> None:
    path = Path(args.db)
    db.add_interaction(args.lead_id, args.type, args.content, args.direction, path)

    lead = db.fetch_lead(args.lead_id, path)
    if not lead:
        raise SystemExit("Lead not found")

    if args.direction == "outbound":
        touch = lead["touch_count"] + 1
        db.update_lead(args.lead_id, {"touch_count": touch, "last_contact_date": args.date, "last_strategy_used": args.message_type}, path)
        db.log_performance(args.lead_id, touch, args.message_type, False, path)

    if args.direction == "inbound":
        perf = db.performance_summary(path)
        print("Immediate alert: inbound lead response received.")
        print(json.dumps(engine.performance_insights(perf), indent=2))

    print("Post interaction decision:", ", ".join(engine.post_interaction_options()))


def _cmd_queue(args: argparse.Namespace) -> None:
    result = engine.evaluate_all_leads(Path(args.db))
    print("Approval Queue")
    for item in result["followup_queue"]:
        print("-" * 70)
        print(f"Lead: {item['lead_name']} (ID {item['lead_id']})")
        print("Last:", item["last_message"] or "<none>")
        print("New:", item["new_message"])
        print("Objective:", item["objective"])
        print("Reasoning:", item["reasoning"])
        print("Options:", ", ".join(item["options"]))
        print(item["touch_notice"])


def _cmd_call_list(args: argparse.Namespace) -> None:
    result = engine.evaluate_all_leads(Path(args.db))
    print("Call List (all hot leads)")
    for entry in result["call_list"]:
        brief = result["call_briefs"][entry["lead_id"]]
        print("-" * 70)
        print(f"{entry['name']} ({entry['phone']}) priority={entry['priority_score']}")
        print("Why hot:", brief["why_hot"])
        print("Objective:", brief["objective"])
        print("3 Questions:")
        for q in brief["three_questions"]:
            print(f"  - {q}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automated CRM decision + escalation engine")
    parser.add_argument("--db", default="crm.db", help="SQLite file path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--seed", action="store_true")
    p_init.set_defaults(func=_cmd_init)

    p_daily = sub.add_parser("daily-run")
    p_daily.set_defaults(func=_cmd_daily)

    p_add = sub.add_parser("add-interaction")
    p_add.add_argument("lead_id", type=int)
    p_add.add_argument("--type", choices=["text", "call", "note"], required=True)
    p_add.add_argument("--direction", choices=["inbound", "outbound"], required=True)
    p_add.add_argument("--content", required=True)
    p_add.add_argument("--message-type", default="follow-up")
    p_add.add_argument("--date", default=None)
    p_add.set_defaults(func=_cmd_add_interaction)

    p_queue = sub.add_parser("queue")
    p_queue.set_defaults(func=_cmd_queue)

    p_call = sub.add_parser("call-list")
    p_call.set_defaults(func=_cmd_call_list)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
