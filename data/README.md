# OMKデータディレクトリ

このディレクトリは、OMKが生成・保持する計測データの標準保存先です。実際の計測値、
CSV、Parquet、SQLite、状態ファイルはGitで管理しません。Gitにはこの説明と、空の標準構造を
保つ必要がある`.gitkeep`だけを登録します。

想定するサブディレクトリは次のとおりです。

- `broute-meter/`: ホスト上のBルートサービスによるCSV、回復状態などの固有一次保存。MQTT経由のJSONLとは別経路であり、Git管理外
- `sensors/`: `sensor-collector`がMQTTメッセージを`YYYY/MM/DD.jsonl`へ保存する一次データ。Git管理外
- `latest/`: collectorがatomic置換する表示用最新状態JSON。`broute_power.json`、`sen66.json`、`ichijo_power_flow.json`だけを保持し、dashboardが読み込む。Git管理外
- `processed/`: `data-transformer`がJSONLから生成する日付パーティション済みParquetの派生データ。Git管理外
- `errors/transform/`: 変換時に検出した不正・未対応レコードのエラー記録。一次データは変更しない。Git管理外
- `harvest-uploader/`: Harvest送信失敗時の再送用SQLiteキュー。運用データのため削除に注意し、Git管理外

Docker Composeからは、原則としてOMKルート基準の`./data/...`をホスト側パスとして
参照してください。`/opt/omk/data`や`/var/lib/omk`は使用しません。

`latest/`はJSONLの代替や履歴保存ではなく、画面の瞬時値用キャッシュです。collectorが書き込み、dashboardは読み取り専用で参照します。実測JSON本体はGit管理対象外で、読取り途中の内容を公開しないようファイルは同一ディレクトリ内の一時ファイルから原子的に置換されます。

データの保持期間、削除、バックアップ、復元は、運用開始前に別途ルールを定めて
ください。`processed/`は再生成可能な派生データですが、運用中に無断で削除しません。
`broute-meter/`の既存CSVと`harvest-uploader/queue.sqlite3`は特に保護してください。
ログはOMKルートの`logs/`へ分離しています。
