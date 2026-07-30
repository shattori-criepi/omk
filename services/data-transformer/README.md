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

`--date 2026-07-30 --data-root data/sensors`、`--dry-run`、`--log-level DEBUG`も使用できます。計測トピックのBルート`power`、`cumulative-energy`、`interval-energy`、SEN66の`sen66`をParquetへ変換します。`omk/<device_id>/status`は管理情報として意図的に除外し、`ignored`と`ignored_topics`にだけ記録します。未知・不正トピックは`data/errors/transform/<input-date>.jsonl`へ原因と元行を記録します。

出力は `data/processed/<dataset>/date=YYYY-MM-DD/data.parquet` です。データセットは`broute_power`、`broute_cumulative_energy`、`broute_interval_energy`、`sen66`です。日付はBルート瞬時電力では計測時刻、積算電力量では計量時刻、30分値では終了時刻、SEN66ではファームウェアが時計を持たないためcollector受信時刻のJST日付を使用します。各ファイルは一時ファイルから原子的に置換するので、同じ入力の再実行で重複しません。
