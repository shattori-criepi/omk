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

## Raspberry Piでの5分間隔実行

DockerではなくRaspberry Piホストのsystemd timerで実行します。リポジトリルートで次を実行してください。runtime依存のみを使うため、セットアップは`requirements.txt`をインストールします（`requirements-dev.txt`は使いません）。

```bash
chmod +x scripts/setup-data-transformer.sh scripts/run-data-transformer.sh
./scripts/setup-data-transformer.sh
```

セットアップはPython、venv、pip、`flock`を提供する`util-linux`を確認し、不足時だけ導入します。`services/data-transformer/.venv`、`data/processed`、`data/errors/transform`を作成し、実行ユーザーとリポジトリパスを埋め込んだunitを`/etc/systemd/system`へ配置します。実行ログは`logs/setup/`にも保存されます。

`omk-data-transformer.timer`は起動約2分後に開始し、その後5分ごとに動作します。JST当日と前日をこの順で処理します。前日処理は日付切替直後に遅れて到着したレコードを取り込むためで、JSONLが存在しない日は「処理対象なし」として正常終了します。一方の日付で失敗してももう一方を処理し、最後に失敗があれば終了コード1として次回timerで再試行します。

同時起動は`data/.data-transformer.lock`への`flock`で防止します。すでに実行中ならその回は正常終了でスキップします。進行中の追記の末尾行が一時的に`invalid_json`になっても、次回の全量再変換で回復します。NULバイトなどの不正行は`data/errors/transform`へ記録され、正常行の変換は継続します。

確認と停止は次のとおりです。

```bash
sudo systemctl status omk-data-transformer.timer --no-pager
systemctl list-timers omk-data-transformer.timer
sudo journalctl -u omk-data-transformer.service --since today --no-pager
sudo systemctl disable --now omk-data-transformer.timer
```

`./scripts/setup-data-transformer.sh --print-units` は`/etc`を書き換えずにservice templateの展開結果を表示します。
