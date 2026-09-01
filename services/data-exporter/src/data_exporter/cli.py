from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from .engine import ExportError, export_parquet


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("YYYY-MM-DD形式で指定してください。") from error


def main() -> None:
    parser = argparse.ArgumentParser(description="OMK processed ParquetをCSV ZIPへ書き出します。")
    parser.add_argument("--from", dest="from_date", required=True, type=_date, metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", required=True, type=_date, metavar="YYYY-MM-DD")
    parser.add_argument("--output-dir", type=Path, default=Path.home() / "omk-exports")
    parser.add_argument("--dataset", action="append", help="対象dataset（複数指定可）")
    parser.add_argument("--processed-root", type=Path, default=Path("data/processed"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        result = export_parquet(args.processed_root, args.output_dir, args.from_date, args.to_date, args.dataset,
                                repository_root=Path.cwd())
    except ExportError as error:
        parser.error(str(error))
    print(result.path)


if __name__ == "__main__":
    main()
