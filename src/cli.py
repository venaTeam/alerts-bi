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
import getpass
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
from src.timefmt import iso_instant
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
    setup = db_subparsers.add_parser(
        "setup",
        help="migrate, then create or update the portal login if PORTAL_SQL_PASSWORD is set; "
        "idempotent, for an init container",
    )
    setup.add_argument("--database", help="target database; default SQL_DATABASE")
    grant = db_subparsers.add_parser(
        "grant-reader",
        help="create or update the portal's read-only SQL login (password: PORTAL_SQL_PASSWORD)",
    )
    grant.add_argument("--database", help="target database; default SQL_DATABASE")
    grant.add_argument("--login", help="reader login; default PORTAL_SQL_USER or alerts_bi_portal")

    # ------------------------------------------------ operator: publication and review
    publish = subparsers.add_parser(
        "publish", help="publish a completed run as its team's weekly review"
    )
    publish.add_argument("--run-id", required=True, help="the completed run to publish")
    publish.add_argument("--note", help="review note shown to readers with this week")
    publish.add_argument(
        "--replace",
        action="store_true",
        help="publish in place of the run already published for exactly this week",
    )
    publish.add_argument(
        "--allow-gap",
        action="store_true",
        help="publish even though the week is not adjacent to the team's published weeks",
    )
    publish.add_argument("--by", help="operator name; defaults to the current user")
    publish.add_argument("--database", help="target database; default SQL_DATABASE")

    unpublish = subparsers.add_parser("unpublish", help="withdraw a published weekly review")
    unpublish.add_argument("--run-id", required=True)
    unpublish.add_argument("--reason", required=True, help="why it is withdrawn; kept for audit")
    unpublish.add_argument("--by", help="operator name; defaults to the current user")
    unpublish.add_argument("--database", help="target database; default SQL_DATABASE")

    publications = subparsers.add_parser(
        "publications", help="list a team's publications, current and withdrawn"
    )
    publications.add_argument("--team", required=True)
    publications.add_argument("--database", help="target database; default SQL_DATABASE")

    decide = subparsers.add_parser(
        "decide", help="record a human decision on one finding of a published week"
    )
    decide.add_argument("--run-id", help="the published week, by run id")
    decide.add_argument("--team", help="the published week, by team ...")
    decide.add_argument("--week", help="... and the UTC date the week ends, YYYY-MM-DD")
    decide.add_argument("--schema", required=True, choices=("v1", "v2"))
    decide.add_argument("--application", required=True)
    decide.add_argument("--key-field", required=True)
    decide.add_argument("--finding", required=True, help="finding id, e.g. R1, R9, P2, OTHER")
    decide.add_argument("--state", required=True, choices=("pending", "confirmed", "dismissed"))
    decide.add_argument("--note", required=True, help="why; readers see it")
    decide.add_argument("--by", help="operator name; defaults to the current user")
    decide.add_argument("--database", help="target database; default SQL_DATABASE")

    decisions = subparsers.add_parser("decisions", help="list a team's decision history")
    decisions.add_argument("--team", required=True)
    decisions.add_argument("--database", help="target database; default SQL_DATABASE")

    weekly = subparsers.add_parser(
        "weekly",
        help="run and publish every due Monday-to-Monday UTC week of every enrolled team",
    )
    weekly.add_argument(
        "--as-of",
        help="treat this instant as now (ISO 8601 UTC); for the fixed-clock mock and backfilling tests",
    )
    weekly.add_argument(
        "--team", action="append", default=[], help="only this enrolled team; repeatable"
    )
    weekly.add_argument(
        "--dry-run", action="store_true", help="show the due weeks without running anything"
    )
    weekly.add_argument("--out", help="report directory root; default out/weekly")
    weekly.add_argument("--registry", help="registry path; default config/teams.json")
    weekly.add_argument("--database", help="target database; default SQL_DATABASE")
    weekly_llm = weekly.add_mutually_exclusive_group()
    weekly_llm.add_argument(
        "--fake-llm", action="store_true", help="use the deterministic fake client (mock only)"
    )
    weekly_llm.add_argument(
        "--no-llm",
        action="store_true",
        help="skip assessment; every week is then held, never auto-published",
    )

    weekly_status = subparsers.add_parser(
        "weekly-status", help="each enrolled team's latest published week and schedule outcome"
    )
    weekly_status.add_argument("--registry", help="registry path; default config/teams.json")
    weekly_status.add_argument("--database", help="target database; default SQL_DATABASE")

    registry = subparsers.add_parser("registry", help="team registry tools")
    registry_subparsers = registry.add_subparsers(dest="registry_command", required=True)
    check = registry_subparsers.add_parser(
        "check", help="validate the registry before deploying an edit"
    )
    check.add_argument("--registry", help="registry path; default config/teams.json")

    admin = subparsers.add_parser(
        "admin", help="serve the operator admin app on loopback, behind a login proxy"
    )
    admin.add_argument("--port", type=int, help="bind port; ADMIN_PORT, else 8200")
    admin.add_argument("--database", help="target database; ADMIN_DATABASE, else SQL_DATABASE")
    admin.add_argument("--registry", help="registry path; default config/teams.json")
    admin.add_argument(
        "--dev-user",
        help="local development only: act as this user when no login proxy is in front",
    )

    portal = subparsers.add_parser("portal", help="serve the read-only review portal")
    portal.add_argument("--host", help="bind address; PORTAL_HOST, else loopback")
    portal.add_argument("--port", type=int, help="bind port; PORTAL_PORT, else 8100")
    portal.add_argument("--database", help="database to read; PORTAL_DATABASE, else SQL_DATABASE")

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

    if args.db_command == "setup":
        from src.config.env import read_str
        from src.config.portal import DEFAULT_READER_LOGIN
        from src.db.reader import grant_reader

        database = _target_database(args, config)
        applied, _ = migrate_database(config.sql, database)
        for version in applied:
            sys.stdout.write(f"applied {version}\n")
        if not applied:
            sys.stdout.write("database is up to date\n")
        password = read_str("PORTAL_SQL_PASSWORD")
        if password:
            login = read_str("PORTAL_SQL_USER", DEFAULT_READER_LOGIN)
            grant_reader(config.sql, database, login, password)
            sys.stdout.write(f"{login} can read the portal views of {database}, and nothing else\n")
        else:
            sys.stdout.write("PORTAL_SQL_PASSWORD is not set; the portal login was left as it is\n")
        return 0

    if args.db_command == "grant-reader":
        from src.config.env import read_str
        from src.config.portal import DEFAULT_READER_LOGIN
        from src.db.reader import grant_reader

        database = _target_database(args, config)
        login = args.login or read_str("PORTAL_SQL_USER", DEFAULT_READER_LOGIN)
        # The password comes from the environment only, never a flag, so it stays out of
        # shell history and process listings.
        grant_reader(config.sql, database, login, read_str("PORTAL_SQL_PASSWORD"))
        sys.stdout.write(f"{login} can now read the portal views of {database}, and nothing else\n")
        return 0

    # reset-test: only ever the configured disposable test database.
    reset_test_database(config.sql, config.sql.test_database)
    sys.stdout.write(f"recreated {config.sql.test_database}\n")
    return 0


def _operator(args: argparse.Namespace) -> str:
    return str(args.by or getpass.getuser())


def _instant(value: object) -> str:
    return iso_instant(value)[:16].replace("T", " ") + " UTC" if isinstance(value, datetime) else ""


def _command_publish(args: argparse.Namespace, config: AppConfig) -> int:
    from src.review.publication import PublicationRefused, publish_run

    with connect(config.sql, _target_database(args, config)) as db:
        try:
            result = publish_run(
                db,
                args.run_id,
                published_by=_operator(args),
                note=args.note,
                replace=args.replace,
                allow_gap=args.allow_gap,
            )
        except PublicationRefused as exc:
            sys.stderr.write(f"not published: {exc}\n")
            return 1
    sys.stdout.write(
        f"published {result.team_id}: week {_instant(result.window_start)} to "
        f"{_instant(result.window_end)} (run {result.run_id[:16]})\n"
    )
    if result.replaced_run_id:
        sys.stdout.write(f"withdrew the earlier publication of run {result.replaced_run_id[:16]}\n")
    return 0


def _command_unpublish(args: argparse.Namespace, config: AppConfig) -> int:
    from src.review.publication import PublicationRefused, unpublish_run

    with connect(config.sql, _target_database(args, config)) as db:
        try:
            unpublish_run(db, args.run_id, withdrawn_by=_operator(args), reason=args.reason)
        except PublicationRefused as exc:
            sys.stderr.write(f"not withdrawn: {exc}\n")
            return 1
    sys.stdout.write(f"withdrew run {args.run_id[:16]}; readers no longer see that week\n")
    return 0


def _command_publications(args: argparse.Namespace, config: AppConfig) -> int:
    from src.review.publication import list_publications

    with connect(config.sql, _target_database(args, config)) as db:
        rows = list_publications(db, args.team)
    if not rows:
        sys.stdout.write(f"{args.team} has no publications\n")
        return 0
    for row in rows:
        state = (
            "published"
            if row["withdrawn_at"] is None
            else f"withdrawn {_instant(row['withdrawn_at'])} by {row['withdrawn_by']}: "
            f"{row['withdrawn_reason']}"
        )
        sys.stdout.write(
            f"{_instant(row['window_start'])} to {_instant(row['window_end'])}  "
            f"run {str(row['run_id'])[:16]}  published {_instant(row['published_at'])} by "
            f"{row['published_by']}  [{state}]\n"
        )
    return 0


def _command_decide(args: argparse.Namespace, config: AppConfig) -> int:
    from src.review.decisions import DecisionRefused, record_decision, resolve_published_run

    with connect(config.sql, _target_database(args, config)) as db:
        try:
            run_id = resolve_published_run(
                db, run_id=args.run_id, team_id=args.team, week=args.week
            )
            decision_id = record_decision(
                db,
                run_id=run_id,
                alert_schema=args.schema,
                application=args.application,
                key_field=args.key_field,
                finding_id=args.finding,
                state=args.state,
                note=args.note,
                decided_by=_operator(args),
            )
        except DecisionRefused as exc:
            sys.stderr.write(f"not recorded: {exc}\n")
            return 1
    sys.stdout.write(f"recorded decision {decision_id}: {args.finding} {args.state}\n")
    return 0


def _command_decisions(args: argparse.Namespace, config: AppConfig) -> int:
    from src.review.decisions import list_decisions

    with connect(config.sql, _target_database(args, config)) as db:
        rows = list_decisions(db, args.team)
    if not rows:
        sys.stdout.write(f"{args.team} has no recorded decisions\n")
        return 0
    for row in rows:
        sys.stdout.write(
            f"{_instant(row['decided_at'])}  {row['state']:<9} {row['finding_id']:<5} "
            f"{row['alert_schema']} {row['application']} / {row['key_field']}  "
            f"by {row['decided_by']}: {row['note']}\n"
        )
    return 0


def _parse_instant(text: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _command_weekly(args: argparse.Namespace, config: AppConfig) -> int:
    from src.weekly.runner import WeeklyBusy, run_weekly

    now = datetime.now(UTC)
    if args.as_of:
        parsed = _parse_instant(args.as_of)
        if parsed is None:
            sys.stderr.write(f"--as-of {args.as_of!r} is not a valid ISO 8601 instant\n")
            return 2
        now = parsed

    client: LlmClient | None
    reason: str | None
    if args.fake_llm:
        client, reason = FakeLlmClient(), None
    else:
        client, reason = select_llm_client(config, use_llm=not args.no_llm)

    try:
        outcomes = run_weekly(
            config,
            now=now,
            database=_target_database(args, config),
            llm_client=client,
            llm_disabled_reason=reason,
            registry_path=args.registry,
            teams=args.team,
            out_root=Path(args.out) if args.out else Path("out") / "weekly",
            dry_run=args.dry_run,
        )
    except WeeklyBusy as exc:
        sys.stderr.write(f"{exc}\n")
        return 1

    if not outcomes:
        sys.stdout.write("nothing due\n")
    for outcome in outcomes:
        week = f"week ending {_instant(outcome.window_end)}" if outcome.window_end else "-"
        run = f" run {outcome.run_id[:16]}" if outcome.run_id else ""
        detail = f": {outcome.detail}" if outcome.detail else ""
        sys.stdout.write(f"{outcome.team_id:<24} {outcome.outcome:<9} {week}{run}{detail}\n")
    # Non-zero when a person is needed, so the CronJob shows it as failed.
    return 1 if any(outcome.needs_attention for outcome in outcomes) else 0


def _command_weekly_status(args: argparse.Namespace, config: AppConfig) -> int:
    from src.weekly.runner import enrolled_teams, next_due

    teams = enrolled_teams(args.registry)
    if not teams:
        sys.stdout.write("no team is enrolled for the weekly review\n")
        return 0
    now = datetime.now(UTC)
    with connect(config.sql, _target_database(args, config)) as db:
        for team in teams:
            latest = db.query_one(
                "SELECT MAX(window_end) AS latest FROM review_publications "
                "WHERE team_id = :t AND withdrawn_at IS NULL",
                {"t": team.team_id},
            )
            latest_end = latest["latest"] if latest else None
            last = db.query_one(
                "SELECT TOP 1 outcome, window_end, detail, invoked_at FROM weekly_review_log "
                "WHERE team_id = :t ORDER BY log_id DESC",
                {"t": team.team_id},
            )
            published = _instant(latest_end) if latest_end else "nothing published"
            due = _instant(next_due(latest_end, now))
            outcome = (
                f"{last['outcome']} ({_instant(last['invoked_at'])})"
                + (f": {last['detail']}" if last["detail"] else "")
                if last
                else "never scheduled"
            )
            sys.stdout.write(
                f"{team.team_id:<24} latest published week ends {published}; next due {due}; "
                f"last outcome {outcome}\n"
            )
    return 0


def _command_registry(args: argparse.Namespace, config: AppConfig) -> int:
    from src.registry import RegistryError, load_registry

    try:
        loaded = load_registry(args.registry) if args.registry else load_registry()
    except RegistryError as exc:
        sys.stderr.write(f"registry is invalid: {exc}\n")
        for detail in exc.details:
            sys.stderr.write(f"  {detail}\n")
        return 1
    sys.stdout.write(
        f"registry {loaded.registry_version} is valid: {len(loaded.teams)} teams, "
        f"sha256 {loaded.file_sha256[:16]}\n"
    )
    for team in loaded.teams:
        enrolled = "weekly" if team.weekly_review else "manual"
        sys.stdout.write(f"  {team.team_id:<28} {enrolled}\n")
    return 0


def _command_admin(args: argparse.Namespace, config: AppConfig) -> int:
    import uvicorn

    from src.admin.app import build_admin
    from src.config.admin import load_admin_settings

    settings = load_admin_settings(
        config,
        port=args.port,
        database=args.database,
        dev_user=args.dev_user,
        registry_path=args.registry,
    )
    if settings.dev_user:
        sys.stderr.write(
            f"warning: --dev-user acts as {settings.dev_user!r} for any request without a login "
            "header; never use it behind a shared proxy\n"
        )
    sys.stdout.write(
        f"alerts-bi admin on http://{settings.host}:{settings.port} (loopback only; put the "
        f"login proxy in front), writing {settings.database}\n"
    )
    uvicorn.run(
        build_admin(settings),
        host=settings.host,
        port=settings.port,
        log_level="warning",
        server_header=False,
    )
    return 0


def _command_portal(args: argparse.Namespace, config: AppConfig) -> int:
    from src.config import load_portal_settings
    from src.portal.server import PortalRefused, serve

    settings = load_portal_settings(config, host=args.host, port=args.port, database=args.database)
    sys.stdout.write(
        f"alerts-bi portal on http://{settings.host}:{settings.port}  (ctrl-c to stop)\n"
        f"  reading {settings.database} as {settings.sql.user}; admitting "
        f"{', '.join(settings.allowed_networks)}\n"
    )
    try:
        serve(settings)
    except PortalRefused as exc:
        sys.stderr.write(f"portal not started: {exc}\n")
        return 1
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
    handlers = {
        "publish": _command_publish,
        "unpublish": _command_unpublish,
        "publications": _command_publications,
        "decide": _command_decide,
        "decisions": _command_decisions,
        "portal": _command_portal,
        "weekly": _command_weekly,
        "weekly-status": _command_weekly_status,
        "registry": _command_registry,
        "admin": _command_admin,
    }
    if args.command in handlers:
        return handlers[args.command](args, config)

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
