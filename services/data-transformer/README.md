# data-transformer

`sensor-collector` が保存した日次JSONLを、分析用のParquetへ変換する独立コンポーネントです。collectorの一次データは変更しません。

```bash
cd services/data-transformer
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=src .venv/bin/python -m data_transformer --input ../../data/sensors/2026/07/30.jsonl --output ../../data/processed
PYTHONPATH=src .venv/bin/pytest -v
```

Raspberry Pi 4（Python 3.13.5、aarch64）では`pyarrow==20.0.0`、`duckdb==1.3.2`、`pytest==9.1.1`で検証しています。

## Parquet verification

DuckDB CLIは不要です。リポジトリ直下から、この仮想環境のPython版DuckDBで読み取り専用の検証を実行できます。

```bash
services/data-transformer/.venv/bin/python scripts/verify_parquet.py
```

`services/data-transformer`から実行する場合は、`.venv/bin/python ../../scripts/verify_parquet.py`です。

対象は`sen66`、`broute_power`、`broute_cumulative_energy`、`broute_interval_energy`です。全日付パーティションを自動で横断し、基本情報、パーティション別件数、NULL・時間別集計、SEN66受信間隔、30分値と積算差分の照合、SEN66と瞬時電力のASOF結合を表示します。Parquetや外部DBファイルを変更しません。別の出力先を確認する場合は`--data-root /path/to/processed`を指定できます。

`--date 2026-07-30 --data-root data/sensors`、`--dry-run`、`--log-level DEBUG`も使用できます。計測トピックのBルート`power`、`cumulative-energy`、`interval-energy`、SEN66の`sen66`をParquetへ変換します。`omk/<device_id>/status`は管理情報として意図的に除外し、`ignored`と`ignored_topics`にだけ記録します。未知・不正トピックは`data/errors/transform/<input-date>.jsonl`へ原因と元行を記録します。

出力は `data/processed/<dataset>/date=YYYY-MM-DD/data.parquet` です。データセットは`broute_power`、`broute_cumulative_energy`、`broute_interval_energy`、`sen66`です。日付はBルート瞬時電力では計測時刻、積算電力量では計量時刻、30分値では終了時刻、SEN66ではファームウェアが時計を持たないためcollector受信時刻のJST日付を使用します。各ファイルは一時ファイルから原子的に置換するので、同じ入力の再実行で重複しません。
