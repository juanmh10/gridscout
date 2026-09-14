"""Technical entry point for data-only high-volume post-processing."""

from __future__ import annotations

import argparse
import asyncio
import json

from packages.pipeline.dataset_analysis import analyze_pipeline_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze persisted high-volume discovery data without marketplace navigation.")
    parser.add_argument("--pipeline-run", required=True, help="Technical pipeline run identifier")
    parser.add_argument("--force", action="store_true", help="Regenerate the aggregate and agent summary")
    args = parser.parse_args()
    result = asyncio.run(analyze_pipeline_dataset(args.pipeline_run, force=args.force))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
