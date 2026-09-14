"""Project persisted high-volume cards into unaudited listings without OLX I/O.

Run with ``--dry-run`` first.  The operation is idempotent and only reads
stored discoveries before creating/updating local projections.
"""

from __future__ import annotations

import argparse

from packages.core.database import SessionLocal
from packages.core.models import PipelineRun
from packages.pipeline.high_volume import reanalyze_high_volume_run


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill card-only listing projections.")
    parser.add_argument("--limit", type=int, default=20, help="Recent terminal high-volume runs to inspect.")
    parser.add_argument("--dry-run", action="store_true", help="List eligible runs without changing data.")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        runs = (
            db.query(PipelineRun)
            .filter(PipelineRun.workload_mode == "high_volume", PipelineRun.status.in_(("completed", "completed_partial", "failed", "blocked")))
            .order_by(PipelineRun.started_at.desc())
            .limit(max(1, args.limit))
            .all()
        )
        if args.dry_run:
            for run in runs:
                print(f"{run.id}: {len(run.discoveries)} discoveries")
            return
        run_ids = [run.id for run in runs]
    finally:
        db.close()
    for run_id in run_ids:
        result = reanalyze_high_volume_run(run_id)
        print(f"{run_id}: published={result.get('cards_published', 0)} preliminary={result.get('preliminary_opportunities', 0)}")


if __name__ == "__main__":
    main()
