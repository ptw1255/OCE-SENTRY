"""Entry point: `python -m oce_sentry`."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .auth import AuthError, TokenProvider
from .config import ConfigError, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oce-sentry",
        description="Live incident console for ODSP on-call engineers.",
    )
    parser.add_argument("--version", action="version", version=f"oce-sentry {__version__}")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Fetch once, print the queue, exit. Works without a terminal UI.",
    )
    parser.add_argument(
        "--incident",
        metavar="ID",
        help="Show one incident and the runbooks that match it.",
    )
    parser.add_argument(
        "--run",
        metavar="ACTION_ID",
        help="Run an action against --incident. Requires an explicit incident id.",
    )
    parser.add_argument(
        "--skill",
        metavar="SKILL_ID",
        help="Run a skill against --incident through Copilot CLI.",
    )
    parser.add_argument(
        "--skills",
        action="store_true",
        help="List discovered skills and exit.",
    )
    parser.add_argument(
        "--slis",
        action="store_true",
        help="Show service level indicators and exit.",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Trailing window for --slis (default 24).",
    )
    parser.add_argument(
        "--kits",
        action="store_true",
        help="Show the investigation kit inventory and exit.",
    )
    parser.add_argument(
        "--bugs",
        action="store_true",
        help="Show tracked ADO bugs and exit.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="With --bugs, include closed items.",
    )
    parser.add_argument(
        "--create-bug",
        metavar="TEXT",
        help="File a bug from a description. Drafted first, then created.",
    )
    parser.add_argument(
        "--category",
        default="other",
        choices=["noise", "tsg", "routing", "process", "other"],
        help="Category for --create-bug (default other).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --create-bug, show the work item without creating it.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Rows to print in --once mode (default 50).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252, and both Copilot output and incident
    # titles routinely contain characters it cannot encode. Without this, a
    # successful run dies while printing its own result.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass

    args = build_parser().parse_args(argv)

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"config: {exc}", file=sys.stderr)
        return 2

    tokens = TokenProvider()

    try:
        if args.kits:
            from .cli import render_kits

            return render_kits(config)

        if args.bugs:
            from .cli import render_bugs

            return render_bugs(config, tokens, show_all=args.all)

        if args.create_bug:
            from .cli import create_bug_cli

            return create_bug_cli(
                config,
                tokens,
                note=args.create_bug,
                category=args.category,
                incident_id=args.incident,
                dry_run=args.dry_run,
            )

        if args.slis:
            from .cli import render_slis

            return render_slis(config, tokens, hours=args.hours)

        if args.skills:
            from .skills import discover_skills

            for skill in discover_skills(config):
                state = skill.error or ("needs shell" if skill.needs_shell else "read-only")
                print(f"{skill.id:26} [{skill.source}] {state}")
                if skill.description:
                    print(f"  {skill.description}")
            return 0

        if args.skill:
            if not args.incident:
                print("--skill requires --incident", file=sys.stderr)
                return 2
            from .cli import run_once_skill

            return run_once_skill(config, tokens, args.incident, args.skill)

        if args.run:
            if not args.incident:
                print("--run requires --incident", file=sys.stderr)
                return 2
            from .cli import run_once_action

            return run_once_action(config, tokens, args.incident, args.run)

        if args.incident:
            from .cli import render_actions

            return render_actions(config, tokens, args.incident)

        if args.once:
            from .cli import render_once

            return render_once(config, tokens, limit=args.limit)

        from .tui.app import run_app

        return run_app(config, tokens)
    except AuthError as exc:
        print(f"auth: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())



