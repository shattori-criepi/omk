from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

from .transformer import transform


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert OMK sensor JSONL to partitioned Parquet.")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--input", type=Path, help="JSONL input file")
    selection.add_argument("--date", type=date.fromisoformat, help="JST collector date (YYYY-MM-DD)")
    parser.add_argument("--data-root", type=Path, default=Path("data/sensors"), help="collector JSONL root for --date")
    parser.add_argument("--output", type=Path, default=Path("data/processed"), help="Parquet output root")
    parser.add_argument("--error-output", type=Path, help="invalid-record JSONL root")
    parser.add_argument("--dry-run", action="store_true", help="validate and report without writing files")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    input_file = args.input or args.data_root / args.date.strftime("%Y") / args.date.strftime("%m") / f"{args.date:%d}.jsonl"
    result = transform(input_file, args.output, dry_run=args.dry_run, error_root=args.error_output)
    print(f"converted={result.converted} skipped={result.skipped} ignored={result.ignored} datasets={dict(result.written)}")
