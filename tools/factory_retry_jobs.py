"""Inspect or explicitly cancel/reconcile one Factory deferred-503 job.

The CLI does not use Gemini or Telegram. It cannot infer that a crashed worker
stopped or that an unconfirmed Telegram send failed; those facts must be
verified independently before invoking a mutation.

From repository root:
  python -m tools.factory_retry_jobs --db PATH --chat-id ID --source-id ID --url URL status
  python -m tools.factory_retry_jobs ... cancel --human-verified --apply
  python -m tools.factory_retry_jobs ... reset-running --human-verified --apply
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from services.shorts_factory_delivery_ledger import FactoryDeliveryJournal
from services.shorts_factory_retry_store import FactoryDeferredRetryStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded Factory provider-503 recovery journal")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--chat-id", required=True, type=int)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--url", required=True)
    operations = parser.add_subparsers(dest="command", required=True)
    operations.add_parser("status", help="Show exact stored state without contacting Telegram")
    for name in ("cancel", "reset-running"):
        item = operations.add_parser(name)
        item.add_argument("--human-verified", action="store_true")
        item.add_argument("--apply", action="store_true")
        if name == "reset-running":
            item.add_argument("--owner", required=True, help="Exact worker owner from status")
            item.add_argument("--pid", required=True, type=int, help="Exact PID independently verified stopped")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.db.is_file():
        parser.error("Database file does not exist; will not create or mutate a new journal")
    journal = FactoryDeliveryJournal(chat_id=args.chat_id, source_id=args.source_id, source_url=args.url, path=args.db)
    store = FactoryDeferredRetryStore(args.db)
    if args.command == "status":
        print(
            json.dumps(
                {
                    "scope": journal.scope,
                    "retry": store.status(journal.scope),
                    "active_lease": journal.job_lease_status(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not args.apply or not args.human_verified:
        parser.error("Mutation requires BOTH --human-verified and --apply")
    if args.command == "cancel":
        store.cancel(journal.scope)
    else:
        store.operator_reset_crashed_running(
            journal.scope,
            expected_owner=args.owner,
            expected_pid=args.pid,
            verified_stopped=True,
        )
    print("Verified provider-retry change applied; no provider or Telegram requests made")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
