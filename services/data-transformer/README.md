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

## Parquet検証

DuckDB CLIは不要です。リポジトリ直下から、この仮想環境のPython版DuckDBで読み取り専用の検証を実行できます。

```bash
services/data-transformer/.venv/bin/python scripts/verify_parquet.py
```

`services/data-transformer`から実行する場合は、`.venv/bin/python ../../scripts/verify_parquet.py`です。

対象は`sen66`、`broute_power`、`broute_cumulative_energy`、`broute_interval_energy`です。全日付パーティションを自動で横断し、基本情報、パーティション別件数、NULL・時間別集計、SEN66受信間隔、30分値と積算差分の照合、SEN66と瞬時電力のASOF結合を表示します。Parquetや外部DBファイルを変更しません。別の出力先を確認する場合は`--data-root /path/to/processed`を指定できます。

## 変換対象と出力

変換CLIの`python -m data_transformer`では、`--input`の代わりに`--date 2026-07-30 --data-root data/sensors`を指定できます。`--dry-run`、`--log-level DEBUG`も変換CLIのオプションです。これらは上記の`verify_parquet.py`のオプションではありません。パスはコマンドを実行するディレクトリからの相対パスです。

計測トピックのBルート`power`、`cumulative-energy`、`interval-energy`、SEN66の`sen66`、Ichijoの`power-flow`、BLEの`environment`、`motion`、`contact`、`power`をParquetへ変換します。BLEは旧payloadの`sensor_id`を`device_id`として正規化し、`measured_at`がない場合はcollector受信時刻を使用します。`/power`はpayloadの`net_power_w`をBルート、`power_w`をBLEとして識別します。`omk/<device_id>/status`、`omk/node/<node_id>/status`、`omk/node/<node_id>/registration/status`、`omk/node/<node_id>/registration/ack`、`omk/node/<node_id>/registration/config`は管理情報として意図的に除外し、`ignored`と`ignored_topics`にだけ記録します。未知・不正トピックは`data/errors/transform/YYYY-MM-DD.jsonl`へ原因と元行を記録します。

出力は `data/processed/<dataset>/date=YYYY-MM-DD/data.parquet` です。データセットは`broute_power`、`broute_cumulative_energy`、`broute_interval_energy`、`sen66`、`ichijo_power_flow`、`ble_environment`、`ble_motion`、`ble_contact`、`ble_power`です。日付はBルート瞬時電力・BLE・Ichijoでは計測時刻、積算電力量では計量時刻、30分値では終了時刻、SEN66ではファームウェアが時計を持たないためcollector受信時刻のJST日付を使用します。Ichijoの`quality=degraded`レコードは計測値がnullでも`errors`（string list）とともに保持しますが、`quality=normal`では主要計測値を引き続き必須とします。今回の正常行があるpartitionは一時ファイルから原子的に置換します。置換前に同じ`source_file`の既存行だけを外してから今回の行を追加するため、同じ`source_file`表記による入力の再実行では重複せず、遅延レコードの処理で別source file由来の既存行を消しません。

`source_file`は入力パスの文字列表現であり、絶対パスへの正規化や同一ファイルのidentity照合は行いません。既存の出力先へ手動再変換するときは、同じ実ファイルでも相対/絶対パスや実行ディレクトリを変えると別sourceとして追加され得ます。標準timerと同じリポジトリルート・`--date`・`--data-root data/sensors`の指定を維持してください。今回の入力に正常行がないpartitionは置換しないため、入力行の削除を既存Parquetへ反映する機能ではありません。

## Raspberry Piでの1時間間隔実行

DockerではなくRaspberry Piホストのsystemd timerで実行します。リポジトリルートで次を実行してください。runtime依存のみを使うため、セットアップは`requirements.txt`をインストールします（`requirements-dev.txt`は使いません）。

```bash
chmod +x scripts/setup-data-transformer.sh scripts/run-data-transformer.sh
./scripts/setup-data-transformer.sh
```

セットアップはPython、venv、pip、`flock`を提供する`util-linux`を確認し、不足時だけ導入します。`services/data-transformer/.venv`、`data/processed`、`data/errors/transform`を作成し、実行ユーザーとリポジトリパスを埋め込んだunitを`/etc/systemd/system`へ配置します。実行ログは`logs/setup/`にも保存されます。

`omk-data-transformer.timer`は起動約2分後に開始し、その後1時間ごとに動作します。JST当日と前日をこの順で処理します。前日処理は日付切替直後に遅れて到着したレコードを取り込むためで、JSONLが存在しない日は「処理対象なし」として正常終了します。一方の日付で失敗してももう一方を処理し、最後に失敗があれば終了コード1として次回timerで再試行します。

同時起動は`data/.data-transformer.lock`への`flock`で防止します。すでに実行中ならその回は正常終了でスキップします。進行中の追記の末尾行が一時的に`invalid_json`になっても、次回の全量再変換で回復します。NULバイトなどの不正行は`data/errors/transform`へ記録され、正常行の変換は継続します。

確認と停止は次のとおりです。

```bash
sudo systemctl status omk-data-transformer.timer --no-pager
systemctl list-timers omk-data-transformer.timer
sudo journalctl -u omk-data-transformer.service --since today --no-pager
sudo systemctl disable --now omk-data-transformer.timer
```

`./scripts/setup-data-transformer.sh --print-units` は`/etc`を書き換えずにservice templateの展開結果を表示します。
