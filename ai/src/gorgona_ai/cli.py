"""gba-ai: operator and job entrypoints for the AI learning plane.

Job entrypoints (Container Apps Jobs):
  tick           scheduled: enqueue periodic runs, then drain every stage
  pump-evidence  event-driven: Service Bus -> durable ingest runs, then drain ingest
  run            drain due runs (optionally only some kinds)
  health         exit 1 when unhealthy
  netcheck       DNS/TCP preflight of the environment's dependencies (no database)
Operator commands: bootstrap, migrate, review, promote, rollback, search.

Configuration (environment): GAI_DATABASE_URL (worker role), GAI_MIGRATION_DATABASE_URL
(owner), GAI_ADMIN_DATABASE_URL + GAI_OWNER_PASSWORD + GAI_WORKER_PASSWORD (bootstrap),
GAI_DATABASE_NAME, GAI_SERVICEBUS_NAMESPACE, GAI_EVIDENCE_QUEUE, AZURE_CLIENT_ID,
GAI_NETCHECK_TARGETS + GAI_NETCHECK_SECRETS (netcheck).
"""

import argparse
import json
import os
import socket
import sys
from collections.abc import Sequence
from uuid import UUID

import psycopg

from gorgona_ai import db, evidence, netcheck, pipeline
from gorgona_ai.intake import ServiceBusSource, pump
from gorgona_ai.jobs.runtime import drain
from gorgona_ai.observability import configure_logging, health


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _worker() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _connect() -> psycopg.Connection:
    return psycopg.connect(_env("GAI_DATABASE_URL"), autocommit=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gba-ai")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bootstrap")
    sub.add_parser("migrate")
    sub.add_parser("netcheck")
    sub.add_parser("tick")
    sub.add_parser("pump-evidence")
    run = sub.add_parser("run")
    run.add_argument("--kinds", nargs="*")
    hp = sub.add_parser("health")
    hp.add_argument("--stale-minutes", type=int, default=60)
    rv = sub.add_parser("review")
    rv.add_argument("tenant_id", type=UUID)
    rv.add_argument("evidence_id", type=UUID)
    rv.add_argument("decision", choices=["approved", "rejected"])
    rv.add_argument("--reviewer", required=True)
    pr = sub.add_parser("promote")
    pr.add_argument("tenant_id", type=UUID)
    pr.add_argument("model_id", type=UUID)
    pr.add_argument("--by", required=True)
    pr.add_argument("--reason", required=True)
    rb = sub.add_parser("rollback")
    rb.add_argument("tenant_id", type=UUID)
    rb.add_argument("--by", required=True)
    rb.add_argument("--reason", required=True)
    se = sub.add_parser("search")
    se.add_argument("tenant_id", type=UUID)
    se.add_argument("query")
    args = parser.parse_args(argv)
    configure_logging(os.environ.get("GAI_LOG_LEVEL", "INFO"))

    if args.command == "bootstrap":
        db.bootstrap(
            _env("GAI_ADMIN_DATABASE_URL"),
            os.environ.get("GAI_DATABASE_NAME", "gorgona_ai"),
            _env("GAI_OWNER_PASSWORD"),
            _env("GAI_WORKER_PASSWORD"),
        )
        print("bootstrapped AI database roles and database")
        return 0
    if args.command == "migrate":
        applied = db.migrate(_env("GAI_MIGRATION_DATABASE_URL"))
        print("applied: " + (", ".join(applied) if applied else "nothing to apply"))
        return 0
    if args.command == "netcheck":
        secrets = [n for n in os.environ.get("GAI_NETCHECK_SECRETS", "").split(",") if n]
        report = netcheck.check(
            netcheck.parse_targets(_env("GAI_NETCHECK_TARGETS")), os.environ, secrets
        )
        print(json.dumps(report))
        return 0 if report["ok"] else 1
    with _connect() as conn:
        if args.command == "tick":
            pipeline.schedule_periodic(conn)
            print(json.dumps(drain(conn, pipeline.HANDLERS, worker=_worker())))
            return 0
        if args.command == "pump-evidence":
            source = ServiceBusSource(
                _env("GAI_SERVICEBUS_NAMESPACE"),
                os.environ.get("GAI_EVIDENCE_QUEUE", "evidence-events"),
                os.environ.get("AZURE_CLIENT_ID"),
            )
            try:
                counts = pump(conn, source)
            finally:
                source.close()
            drained = drain(conn, pipeline.HANDLERS, worker=_worker(), kinds=["ingest"])
            print(json.dumps({"intake": counts, "drained": drained}))
            return 0
        if args.command == "run":
            print(json.dumps(drain(conn, pipeline.HANDLERS, worker=_worker(), kinds=args.kinds)))
            return 0
        if args.command == "health":
            report = health(conn, stale_minutes=args.stale_minutes)
            print(json.dumps(report))
            return 0 if report["healthy"] else 1
        if args.command == "review":
            evidence.review(conn, args.tenant_id, args.evidence_id, args.decision, args.reviewer)
            print(f"{args.decision}")
            return 0
        if args.command == "promote":
            print(pipeline.promote(conn, args.tenant_id, args.model_id, args.by, args.reason))
            return 0
        if args.command == "rollback":
            print(pipeline.rollback(conn, args.tenant_id, args.by, args.reason))
            return 0
        if args.command == "search":
            print(json.dumps(pipeline.search(conn, args.tenant_id, args.query)))
            return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
