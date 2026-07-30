#!/usr/bin/env python3
"""Run the read-only DuckDB Parquet verification queries."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import duckdb

DATASETS = {
    "sen66": ("sen66", {"device_id", "measured_at", "temperature_c", "relative_humidity_pct", "co2_ppm", "pm2_5_ug_m3"}),
    "broute_power": ("power", {"device_id", "measured_at", "net_power_w"}),
    "broute_cumulative_energy": ("cumulative_energy", {"device_id", "metered_at", "cumulative_energy_import_kwh", "cumulative_energy_export_kwh"}),
    "broute_interval_energy": ("interval_energy", {"device_id", "start_at", "end_at", "import_energy_kwh", "export_energy_kwh", "quality_status"}),
}
SECTION = re.compile(r"^-- SECTION: (.+)$", re.MULTILINE)


def sql_literal(value: str) -> str:
    return value.replace("'", "''")


def parquet_files(data_root: Path, dataset: str) -> list[Path]:
    directory = data_root / dataset
    return sorted(path for path in directory.rglob("*.parquet") if path.is_file()) if directory.is_dir() else []


def validate_inputs(connection: duckdb.DuckDBPyConnection, data_root: Path) -> None:
    for dataset, (_, required_columns) in DATASETS.items():
        files = parquet_files(data_root, dataset)
        if not files:
            raise ValueError(f"Required dataset '{dataset}' has no Parquet files under {data_root / dataset}")
        for file in files:
            columns = {row[0] for row in connection.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(file)]).fetchall()}
            missing = sorted(required_columns - columns)
            if missing:
                raise ValueError(f"Dataset '{dataset}' is missing required columns in {file}: {', '.join(missing)}")


def render_sql(sql: str, data_root: Path) -> str:
    values = {
        "SEN66_PATH": data_root / "sen66" / "**" / "*.parquet",
        "POWER_PATH": data_root / "broute_power" / "**" / "*.parquet",
        "CUMULATIVE_PATH": data_root / "broute_cumulative_energy" / "**" / "*.parquet",
        "INTERVAL_PATH": data_root / "broute_interval_energy" / "**" / "*.parquet",
    }
    for name, path in values.items():
        sql = sql.replace("{{" + name + "}}", sql_literal(str(path)))
    return sql


def sections(sql: str) -> list[tuple[str, str]]:
    markers = list(SECTION.finditer(sql))
    return [(match.group(1), sql[match.end(): markers[index + 1].start() if index + 1 < len(markers) else len(sql)]) for index, match in enumerate(markers)]


def print_result(result: duckdb.DuckDBPyConnection) -> None:
    """Print a compact table without requiring pandas."""
    headers = [column[0] for column in result.description]
    rows = [["NULL" if value is None else str(value) for value in row] for row in result.fetchall()]
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    format_row = lambda row: " | ".join(value.ljust(widths[index]) for index, value in enumerate(row))
    print(format_row(headers))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(format_row(row))


def run(sql_path: Path, data_root: Path) -> int:
    connection = duckdb.connect(":memory:")
    current_statement = "input validation"
    try:
        validate_inputs(connection, data_root)
        sql = render_sql(sql_path.read_text(encoding="utf-8"), data_root)
        # DuckDB parses statements, rather than splitting on semicolons ourselves.
        current_statement = "initial dataset views"
        for statement in connection.extract_statements(sql[: SECTION.search(sql).start()]):
            current_statement = statement.query
            connection.execute(statement.query)
        for title, section_sql in sections(sql):
            print(f"\n=== {title} ===")
            current_statement = title
            for statement in connection.extract_statements(section_sql):
                current_statement = f"{title}: {statement.query.strip()[:120]}"
                print_result(connection.execute(statement.query))
    except Exception as error:
        print(f"Verification failed while executing {current_statement}: {error}", file=sys.stderr)
        return 1
    finally:
        connection.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=repository_root / "data" / "processed", help="Processed Parquet root (default: repository data/processed)")
    parser.add_argument("--sql", type=Path, default=repository_root / "scripts" / "verify_parquet.sql", help="Verification SQL file")
    args = parser.parse_args(argv)
    return run(args.sql.resolve(), args.data_root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
