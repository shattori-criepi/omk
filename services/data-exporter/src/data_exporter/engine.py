"""Stream date-partitioned OMK Parquet data into a portable CSV ZIP."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Iterable
from zoneinfo import ZoneInfo
import zipfile

import pyarrow.parquet as pq


DATASETS = (
    "broute_power",
    "broute_cumulative_energy",
    "broute_interval_energy",
    "sen66",
    "ichijo_power_flow",
    "ble_environment",
    "ble_motion",
    "ble_contact",
    "ble_power",
)
_EXCLUDED_COLUMNS = {"source_file", "source_line_number"}
JST = ZoneInfo("Asia/Tokyo")


class ExportError(RuntimeError):
    """A user-facing failure which never modifies processed Parquet."""


@dataclass(frozen=True)
class ExportResult:
    path: Path
    datasets: tuple[str, ...]
    row_counts: dict[str, int]


def export_parquet(
    processed_root: Path,
    output_dir: Path,
    from_date: date,
    to_date: date,
    datasets: Iterable[str] | None = None,
    *,
    now: datetime | None = None,
    repository_root: Path | None = None,
) -> ExportResult:
    """Create an atomic CSV ZIP using only selected date partitions.

    Each Parquet record batch is converted independently, so an export does not
    load all selected rows into memory.
    """
    if from_date > to_date:
        raise ExportError("開始日は終了日以前にしてください。")
    selected = _select_datasets(datasets)
    partitions = {dataset: _partitions_in_range(processed_root, dataset, from_date, to_date) for dataset in selected}
    if not any(partitions.values()):
        raise ExportError("指定期間にParquetデータがありません。")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ExportError(f"出力ディレクトリを作成できません: {output_dir}: {error}") from error
    if not output_dir.is_dir() or not os.access(output_dir, os.W_OK):
        raise ExportError(f"出力ディレクトリへ書き込めません: {output_dir}")

    exported_at = (now or datetime.now(JST)).astimezone(JST)
    filename = f"omk-export_{from_date:%F}_{to_date:%F}_{exported_at:%Y%m%d-%H%M%S}.zip"
    destination = output_dir / filename
    partial = output_dir / f"{filename}.partial"
    row_counts: dict[str, int] = {}
    exported_datasets: list[str] = []
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for dataset in selected:
                files = partitions[dataset]
                if not files:
                    continue
                row_counts[dataset] = _write_dataset_csv(archive, dataset, files)
                exported_datasets.append(dataset)
            manifest = {
                "format_version": 1,
                "exported_at": exported_at.isoformat(),
                "timezone": "Asia/Tokyo",
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "datasets": exported_datasets,
                "row_counts": row_counts,
            }
            git_commit = _git_commit(repository_root)
            if git_commit:
                manifest["git_commit"] = git_commit
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        os.replace(partial, destination)
    except Exception as error:
        # The broad conversion is intentional: Arrow, CSV, ENOSPC and ZIP
        # failures all leave no file that looks like a completed export.
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(error, ExportError):
            raise
        raise ExportError(f"ZIP/CSVの生成に失敗しました: {error}") from error
    return ExportResult(destination, tuple(exported_datasets), row_counts)


def _select_datasets(datasets: Iterable[str] | None) -> tuple[str, ...]:
    if datasets is None:
        return DATASETS
    selected = tuple(dict.fromkeys(datasets))
    invalid = sorted(set(selected) - set(DATASETS))
    if invalid:
        raise ExportError(f"未知のdatasetです: {', '.join(invalid)}")
    if not selected:
        raise ExportError("datasetを少なくとも1つ指定してください。")
    return selected


def _partitions_in_range(root: Path, dataset: str, from_date: date, to_date: date) -> list[Path]:
    directory = root / dataset
    if not directory.is_dir():
        return []
    files: list[tuple[date, Path]] = []
    for partition in directory.glob("date=*"):
        if not partition.is_dir():
            continue
        try:
            partition_date = date.fromisoformat(partition.name.removeprefix("date="))
        except ValueError:
            continue
        parquet = partition / "data.parquet"
        if from_date <= partition_date <= to_date and parquet.is_file():
            files.append((partition_date, parquet))
    return [path for _, path in sorted(files)]


def _write_dataset_csv(archive: zipfile.ZipFile, dataset: str, files: list[Path]) -> int:
    schema = pq.ParquetFile(files[0]).schema_arrow
    columns = [field.name for field in schema if field.name not in _EXCLUDED_COLUMNS]
    rows = 0
    with archive.open(f"{dataset}.csv", "w") as binary:
        # TextIOWrapper writes the BOM exactly once and csv handles quoting.
        import io
        with io.TextIOWrapper(binary, encoding="utf-8-sig", newline="") as text:
            writer = csv.writer(text, lineterminator="\n")
            writer.writerow(columns)
            for parquet_path in files:
                parquet = pq.ParquetFile(parquet_path)
                available = set(parquet.schema_arrow.names)
                missing = set(columns) - available
                if missing:
                    raise ExportError(f"{parquet_path} に必要な列がありません: {', '.join(sorted(missing))}")
                for batch in parquet.iter_batches(columns=columns, batch_size=8192):
                    for record in batch.to_pylist():
                        writer.writerow([_csv_value(name, record[name]) for name in columns])
                        rows += 1
    return rows


def _csv_value(column: str, value: Any) -> Any:
    if value is None:
        return ""
    if column == "errors" and isinstance(value, list):
        return ";".join(str(item) for item in value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _git_commit(repository_root: Path | None) -> str | None:
    if repository_root is None:
        return None
    try:
        return subprocess.run(
            ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
