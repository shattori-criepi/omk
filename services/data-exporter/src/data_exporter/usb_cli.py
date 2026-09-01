from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

from .engine import ExportError
from .usb import UsbExportError, UsbExportService, UsbLocator, privileged_helper


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("YYYY-MM-DD形式で指定してください。") from error


def main() -> None:
    parser = argparse.ArgumentParser(description="OMK CSV ZIPを対応USBメモリへ安全に書き出します。")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--from", dest="from_date", type=_date, metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", type=_date, metavar="YYYY-MM-DD")
    parser.add_argument("--dataset", action="append")
    parser.add_argument("--processed-root", type=Path, default=Path("data/processed"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    locator = UsbLocator()
    try:
        if args.status:
            if args.from_date or args.to_date:
                parser.error("--statusは期間指定と併用できません。")
            print(json.dumps(locator.status().as_dict(), ensure_ascii=False))
            return
        if args.from_date is None or args.to_date is None:
            parser.error("--from と --to を指定してください。")
        service = UsbExportService(args.processed_root, locator,
                                   privileged_helper(Path("/usr/local/libexec/omk-export-usb-helper")))
        result = service.export(args.from_date, args.to_date, args.dataset)
        print(result.path)
    except (UsbExportError, ExportError) as error:
        print(f"ERROR: {error.code}: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
