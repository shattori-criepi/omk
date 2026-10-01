# data-transformer

`sensor-collector` が保存した日次JSONLを、分析用のParquetへ変換する独立コンポーネントです。collectorの一次データは変更しません。

```bash
cd services/data-transformer
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=src .venv/bin/python -m data_transformer --input ../../data/sensors/2026/07/30.jsonl --output /tmp/omk-parquet-example
PYTHONPATH=src .venv/bin/pytest -v
```

上の単体変換例は試験用出力先を使います。稼働中の`data/processed`を再処理する場合は、下記の入力パス規約を維持してください。

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

### 入力パスと再処理の制約（再現確認済み）

処理済みcheckpointや別state fileはなく、毎回JSONL全体を読みます。Parquetの`source_file`に入力パスの文字列表現を保存し、merge時は今回と文字列が一致する行だけを置換します。`source_line_number`は来歴であり、record-levelの重複排除キーではありません。mtime、size、content hash、inode、timestamp/device IDによる同一性判定も行いません。

同じJSONLを同じ出力先へ別表記で投入すると、変換対象の正常行がdataset/date partitionごとに追加されます。別プロセスでの再実行でも同じ結果です。同じ表記を繰り返すだけなら増殖しませんが、既に別表記で保存された行は残ります。今回の入力に正常行がないpartitionは置換しないため、入力行の削除を既存Parquetへ反映する機能でもありません。

Python 3.13.15、固定依存のPyArrow 20.0.0 / DuckDB 1.3.2で、次を一時JSONLと新しい出力先だけを使って確認しました。

| 同じ実ファイルへの表記変更 | 再実行後 |
| --- | --- |
| 同一表記、先頭`./`、末尾スラッシュ | 重複なし。後二者はCLIの`Path`変換で除去される |
| 相対→絶対、`data/../data/...` | 重複する |
| ファイルsymlink、ディレクトリsymlink経由 | 重複する |
| リポジトリルートの`data/sensors/...`→サービス配下の`../../data/sensors/...` | 重複する |

再現試験では同一timestamp/device IDの30分値が2行になり、買電量合計も1.5 kWhから3.0 kWhへ増えました。Dashboardの日計はParquetを合計するため影響を受け、CSV exportにも重複が出ます。元JSONL、latest cache、MQTTを直接購読するHarvestの集約は、このParquet再変換では変更しません。

標準timerはホストのリポジトリルートで`--date`・`--data-root data/sensors`を使用します。同じ構成でのservice restart、Gateway reboot、setup再実行、通常updateは入力表記を変えません。collectorのDocker内`/app/data/sensors`は入力JSONL内のsource識別子ではなく、通常のtransformerはDocker外で動きます。一方、手動復旧、`--input`への切替、絶対パスの指定、別cwdからの実行、独自container/mount構成への変更では発生し得ます。

本番の手動再処理も標準timerと同じcwd・入力指定を使い、timerとの同時実行は避けてください。これは新しい別表記を増やさないための暫定運用であり、既存の重複は修復しません。

### path正規化を導入する際の互換性

新規保存だけを`Path.resolve()`へ変更すると、旧相対`source_file`と一致せず初回mergeで二重化します。旧値まで現在のcwd/symlinkで解決すると、当時と異なる実体へ対応付けて別入力の行を消す危険があります。旧Parquetは当時のcwdやsymlink先を記録しておらず、異なるcwdにある別ファイルから同一の`source_file`・行内容が生成されるケースも確認しました。この情報だけから安全な自動移行はできません。

修正候補は、新しい入力について解決済み絶対パスを保存し、同一表記差を同じ入力にする方法です。symlinkの実体切替やファイル移動は別入力として扱い、同内容の別ファイルをhashでまとめません。ただし導入には、旧形式を識別して無条件mergeを止める仕組みと、確認済みの旧source対応付け、または元JSONLが揃った状態での新しい出力先への全量再生成・比較・明示的切替が必要です。リポジトリ移動やmount先変更も移行対象になります。今回、機能コードの変更や既存Parquetの移行・修復は行っていません。

### 既存Parquetの重複候補を調べる

次はDuckDBで実行する読み取り専用SQLです。対象datasetの全partitionから、`source_file`以外の全列（`source_line_number`を含む）が同じで、source表記が複数ある行を探します。Python版DuckDBの`execute()`でも実行できます。ファイルがないdatasetには実行しません。

```sql
SELECT * EXCLUDE (source_file),
       count(*) AS copies,
       list(DISTINCT source_file) AS source_paths
FROM read_parquet('data/processed/broute_interval_energy/date=*/data.parquet')
GROUP BY ALL
HAVING count(DISTINCT source_file) > 1;
```

これは候補検出であり、同じtimestamp/device IDだけで削除を判断しません。正当な別入力に同一行がある場合も候補になり、行順変更等による重複は検出できないことがあります。元JSONL、当時の実行場所、source表記を照合して修復範囲を決めます。今回の作業環境には既存のJSONL/Parquetがなく、稼働Gatewayで既に発生しているかは未確認です。

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
