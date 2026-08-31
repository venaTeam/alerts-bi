"""Alerts BI command line.

A run always names one team. There is deliberately no "all teams" mode: the MVP reports one
selected team per run, and a default that fanned out would be a scope change hiding in a
convenience.

Failure behaviour follows the blueprint: an invalid registry fails before any alert query,
a failed Elasticsearch query fails the run rather than publishing a partial scorecard as
complete, a failed persistence step fails the run rather than rendering from memory, and a
rendering failure after persistence leaves the analysis committed so rendering can be
retried.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from src.config import AppConfig, load_config
from src.db.connection import connect
from src.db.migrate import (
    applied_migrations,
    current_revision,
    heads,
    load_migrations,
    migrate_database,
    reset_test_database,
)
from src.db.repositories import get_latest_run, persist_run
from src.es.client import EsClient
from src.llm.client import LlmClient
from src.llm.fake import FakeLlmClient
from src.logging_setup import log, redact_error
from src.report.render import render_run_report
from src.run.orchestrator import execute_run, select_llm_client
from src.versions import APP_VERSION

__all__ = ["build_parser", "main", "run_cli"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alerts-bi",
        description="Single-team weekly alert quality and migration scorecard.",
    )
    parser.add_argument("--version", action="version", version=f"alerts-bi {APP_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="analyse one team, persist, and render")
    run.add_argument("--team", required=True, help="registry team_id; never defaults")
    run.add_argument("--run-at", help="freeze run_at (ISO 8601 UTC); defaults to now")
    run.add_argument("--out", help="output directory; default out/<run id prefix>")
    run.add_argument("--registry", help="registry path; default config/teams.json")
    run.add_argument("--database", help="target database; default SQL_DATABASE")
    llm = run.add_mutually_exclusive_group()
    llm.add_argument(
        "--fake-llm",
        action="store_true",
        help="use the deterministic fake client instead of the on-prem model",
    )
    llm.add_argument(
        "--no-llm",
        action="store_true",
        help="skip assessment; eligible identities become unassessed with a reason",
    )

    report = subparsers.add_parser("report", help="re-render a stored run from SQL only")
    report.add_argument("--run-id", help="run to render")
    report.add_argument("--team", help="render that team's most recent completed run")
    report.add_argument("--out", help="output directory")
    report.add_argument("--database", help="target database; default SQL_DATABASE")

    database = subparsers.add_parser("db", help="database maintenance")
    db_subparsers = database.add_subparsers(dest="db_command", required=True)
    for name, help_text in (
        ("migrate", "create the database if absent and apply pending migrations"),
        ("status", "show which migrations are applied"),
    ):
        sub = db_subparsers.add_parser(name, help=help_text)
        sub.add_argument("--database", help="target database; default SQL_DATABASE")
    db_subparsers.add_parser(
        "reset-test", help="drop and recreate ONLY the configured disposable test database"
    )

    api = subparsers.add_parser("serve", help="serve the HTTP trigger surface")
    api.add_argument(
        "--host",
        help="bind address; API_HOST, else loopback, because the surface has no authentication",
    )
    api.add_argument("--port", type=int, help="bind port; API_PORT, else 8000")
    api.add_argument("--registry", help="registry path; default config/teams.json")
    api.add_argument("--database", help="target database; default SQL_DATABASE")

    verify = subparsers.add_parser(
        "verify-acceptance", help="compare persisted rows and CSVs against the manifest"
    )
    verify.add_argument("--manifest", help="path to expected-results.json")
    verify.add_argument("--out", help="output directory for the rendered reports")
    verify.add_argument("--database", help="target database; default SQL_DATABASE")

    return parser


def _target_database(args: argparse.Namespace, config: AppConfig) -> str:
    return getattr(args, "database", None) or config.sql.database


def _command_run(args: argparse.Namespace, config: AppConfig) -> int:
    run_at = datetime.now(UTC)
    if args.run_at:
        try:
            parsed = datetime.fromisoformat(str(args.run_at).replace("Z", "+00:00"))
        except ValueError:
            sys.stderr.write(f"--run-at {args.run_at!r} is not a valid ISO 8601 instant\n")
            return 2
        run_at = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    # The deterministic fake is opt-in and explicit, so a mock run can never be mistaken
    # for a live one: it stamps its own model_version onto the run record.
    client: LlmClient | None
    reason: str | None
    if args.fake_llm:
        client, reason = FakeLlmClient(), None
    else:
        client, reason = select_llm_client(config, use_llm=not args.no_llm)

    es_client = EsClient(config.es)
    with connect(config.sql, _target_database(args, config)) as db:
        payload, summary = execute_run(
            team_id=args.team,
            run_at=run_at,
            config=config,
            es_client=es_client,
            llm_client=client,
            llm_disabled_reason=reason,
            registry_path=args.registry,
            db=db,
        )

        persist_run(db, payload)
        log.info("run.persisted", run_id=summary.run_id, team_id=summary.team_id)

        out_dir = Path(args.out) if args.out else Path("out") / summary.run_id[:16]
        files = render_run_report(db, summary.run_id, out_dir)

    readiness = (
        "n/a (no v2 identities)" if summary.readiness is None else f"{summary.readiness:.1f}%"
    )
    sys.stdout.write(
        "\n".join(
            [
                f"run_id:        {summary.run_id}",
                f"team:          {summary.team_id}",
                f"phase:         {summary.phase}",
                f"readiness:     {readiness}",
                f"v1:            {summary.v1_rows} rows / {summary.v1_identities} distinct",
                f"v2:            {summary.v2_rows} rows / {summary.v2_identities} distinct",
                f"llm assessed:  {'yes' if summary.llm_assessed else 'no'} "
                f"({summary.llm_eligible} eligible identities)",
                "",
                *[f"wrote {path}" for path in files],
                "",
            ]
        )
    )
    return 0


def _command_report(args: argparse.Namespace, config: AppConfig) -> int:
    if not args.run_id and not args.team:
        sys.stderr.write("--run-id or --team is required\n")
        return 2

    with connect(config.sql, _target_database(args, config)) as db:
        run_id = args.run_id
        if not run_id:
            latest = get_latest_run(db, args.team)
            if latest is None:
                sys.stderr.write(f"no completed run stored for team {args.team}\n")
                return 1
            run_id = str(latest["run_id"])

        out_dir = Path(args.out) if args.out else Path("out") / run_id[:16]
        files = render_run_report(db, run_id, out_dir)

    sys.stdout.write("\n".join(f"wrote {path}" for path in files) + "\n")
    return 0


def _command_db(args: argparse.Namespace, config: AppConfig) -> int:
    if args.db_command == "migrate":
        database = _target_database(args, config)
        applied, already = migrate_database(config.sql, database)
        for version in applied:
            sys.stdout.write(f"applied {version}\n")
        if not applied:
            sys.stdout.write("database is up to date\n")
        log.info(
            "db.migrate_complete",
            database=database,
            applied=len(applied),
            already_applied=len(already),
        )
        return 0

    if args.db_command == "status":
        database = _target_database(args, config)
        with connect(config.sql, database) as db:
            applied_map = applied_migrations(db)
            revision = current_revision(db)
            sys.stdout.write(f"database: {database}\n")
            sys.stdout.write(f"revision: {revision or '(none - never migrated)'}\n")
            for migration in load_migrations():
                if migration.version not in applied_map:
                    state = "pending"
                elif applied_map[migration.version] == migration.checksum:
                    state = "applied"
                else:
                    state = "APPLIED BUT FILE CHANGED"
                sys.stdout.write(f"  {migration.version:<32} {state}\n")

        # More than one head means two migrations were added without agreeing on an order.
        graph_heads = heads()
        if len(graph_heads) > 1:
            sys.stderr.write(
                f"warning: {len(graph_heads)} revision heads ({', '.join(graph_heads)}); "
                "merge them before migrating\n"
            )
        return 0

    # reset-test: only ever the configured disposable test database.
    reset_test_database(config.sql, config.sql.test_database)
    sys.stdout.write(f"recreated {config.sql.test_database}\n")
    return 0


def _command_serve(args: argparse.Namespace, config: AppConfig) -> int:
    from src.api import serve
    from src.config import load_api_settings

    # Flags override the environment, which overrides the default - the same precedence the
    # rest of the configuration uses.
    settings = load_api_settings(
        config,
        host=args.host,
        port=args.port,
        registry_path=args.registry,
        database=args.database,
    )
    if not settings.loopback_only:
        sys.stderr.write(
            f"warning: binding to {settings.host} exposes an unauthenticated endpoint that "
            "triggers Elasticsearch reads and SQL writes to that network\n"
        )
    sys.stdout.write(
        f"alerts-bi serving on http://{settings.host}:{settings.port}  (ctrl-c to stop)\n"
        f"  interactive API docs: http://{settings.host}:{settings.port}/docs\n"
    )
    serve(settings)
    return 0


def _command_verify(args: argparse.Namespace, config: AppConfig) -> int:
    from src.run.verify import verify_acceptance

    result = verify_acceptance(
        config=config,
        manifest_path=args.manifest,
        out_dir=args.out,
        database=args.database,
    )
    if result.ok:
        sys.stdout.write(f"acceptance verification passed: {result.checks} checks\n")
        return 0

    sys.stderr.write(
        f"acceptance verification FAILED: {len(result.failures)} of {result.checks} checks\n\n"
    )
    for failure in result.failures:
        sys.stderr.write(
            f"  {failure.where}\n"
            f"    expected: {failure.expected!r}\n"
            f"    actual:   {failure.actual!r}\n"
        )
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config()

    if args.command == "run":
        return _command_run(args, config)
    if args.command == "report":
        return _command_report(args, config)
    if args.command == "db":
        return _command_db(args, config)
    if args.command == "serve":
        return _command_serve(args, config)
    if args.command == "verify-acceptance":
        return _command_verify(args, config)

    return 2


def run_cli() -> None:
    """Console entry point."""
    try:
        sys.exit(main())
    except Exception as exc:
        log.error("cli.failed", error=redact_error(exc))
        sys.stderr.write(f"{exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    run_cli()
