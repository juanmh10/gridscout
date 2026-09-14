"""Technical, no-navigation finalizer for a persisted high-volume run."""

from __future__ import annotations

import argparse
import json

from packages.pipeline.high_volume import finalize_high_volume_run


def main() -> None:
    parser = argparse.ArgumentParser(description="Close a high-volume workload while preserving its task queue.")
    parser.add_argument("--pipeline-run", required=True, help="Technical pipeline run identifier")
    parser.add_argument("--reason", default=None, help="Operator-visible close reason")
    args = parser.parse_args()
    result = finalize_high_volume_run(args.pipeline_run, reason=args.reason)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
