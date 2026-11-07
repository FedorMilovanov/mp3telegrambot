"""Read-only inspection and explicitly verified reconciliation for Factory Telegram delivery.

Run from repository root:
  python -m tools.factory_delivery_reconcile --db PATH --chat-id 123 --source-id VIDEO --url URL status
  python -m tools.factory_delivery_reconcile ... confirm \\
      --artifact short:1 --fingerprint SHA --message-id 1234 --human-verified --apply
  python -m tools.factory_delivery_reconcile ... verified-not-delivered \\
      --artifact short:1 --fingerprint SHA --human-verified --apply
  python -m tools.factory_delivery_reconcile ... release-lease --owner TOKEN --pid PID --human-verified --apply

Inspect the actual destination Telegram chat, receipt and process status first.
No command automatically contacts Telegram or infers non-delivery from timeouts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from services.shorts_factory_delivery_ledger import FactoryDeliveryJournal


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Factory delivery journal: inspect; reconcile only after verification")
    parser.add_argument("--db", required=True, type=Path, help="Exact existing Factory delivery ledger SQLite path")
    parser.add_argument("--chat-id", required=True, type=int)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--url", required=True)
    operations = parser.add_subparsers(dest="operation", required=True)
    status = operations.add_parser("status", help="Read-only per-source lease and artifact state")
    status.add_argument("--artifact", help="Optional artifact key, such as short:1 or editorial:pack")
    for name in ("confirm", "verified-not-delivered"):
        target = operations.add_parser(name)
        target.add_argument("--artifact", required=True)
        target.add_argument("--fingerprint", required=True, help="Exact fingerprint from status; do not guess")
        if name == "confirm":
            target.add_argument("--message-id", required=True, type=int, help="Telegram message ID observed in chat")
    release = operations.add_parser("release-lease")
    release.add_argument("--owner", required=True, help="Exact owner token from status")
    release.add_argument("--pid", required=True, type=int, help="Exact process ID confirmed stopped")
    for operation in (operations.choices["confirm"], operations.choices["verified-not-delivered"], release):
        operation.add_argument("--human-verified", action="store_true", help="You verified Telegram/process state")
        operation.add_argument("--apply", action="store_true", help="Persist the confirmed change")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.db.is_file():
        parser.error("Database path does not exist; do not silently create a replacement")
    journal = FactoryDeliveryJournal(
        chat_id=args.chat_id,
        source_id=args.source_id,
        source_url=args.url,
        path=args.db,
    )
    if args.operation == "status":
        print(
            json.dumps(
                {
                    "scope": journal.scope,
                    "job_lease": journal.job_lease_status(),
                    "artifact": journal.status(args.artifact) if args.artifact else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if not args.apply or not args.human_verified:
        parser.error("Mutation requires BOTH --human-verified and --apply, after independent Telegram/process checks")
    if args.operation == "release-lease":
        journal.operator_release_stale_job_lease(
            expected_owner=args.owner,
            expected_process_id=args.pid,
            operator_verified_stopped=True,
        )
    else:
        journal.operator_reconcile_delivery(
            args.artifact,
            expected_fingerprint=args.fingerprint,
            verified_message_id=args.message_id if args.operation == "confirm" else None,
            operator_verified=True,
        )
    print("Applied verified reconciliation; no Telegram API calls or file sends were made")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
