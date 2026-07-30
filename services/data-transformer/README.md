# data-transformer

`sensor-collector` が保存した日次JSONLを、分析用のParquetへ変換する独立コンポーネントです。collectorの一次データは変更しません。

```bash
cd services/data-transformer
python -m venv .venv && .venv/bin/pip install -r requirements.txt
PYTHONPATH=src .venv/bin/python -m data_transformer --input ../../data/sensors/2026/07/30.jsonl --output ../../data/processed
```

`--date 2026-07-30 --data-root data/sensors`、`--dry-run`、`--log-level DEBUG`も使用できます。対応トピックはBルートの`power`、`cumulative-energy`、SEN66の`sen66`です。statusや未知のトピックは無視せず、`data/errors/transform/<input-date>.jsonl`へ原因と元行を記録します。

出力は `data/processed/<dataset>/date=YYYY-MM-DD/data.parquet` です。日付はBルートでは計測・計量時刻（JST）、SEN66はファームウェアが時計を持たないためcollector受信時刻（JST）を使用します。各ファイルは一時ファイルから原子的に置換するので、同じ入力の再実行で重複しません。
