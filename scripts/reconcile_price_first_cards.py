"""Reconcile high-volume card projections without marketplace or model I/O.

Run with ``PYTHONPATH=. python3 scripts/reconcile_price_first_cards.py`` for
the idempotent repair, or pass ``--dry-run`` to inspect its counters first.
"""

from __future__ import annotations

import argparse
import json

from packages.pipeline.high_volume import reconcile_historical_runs


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply the price-first card-gate reconciliation.")
    parser.add_argument("--dry-run", action="store_true", help="Calculate the repair without committing it.")
    args = parser.parse_args()
    print(json.dumps(reconcile_historical_runs(dry_run=args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
