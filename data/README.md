# OMKデータディレクトリ

このディレクトリは、OMKが生成・保持する計測データの標準保存先です。実際の計測値、
CSV、データベースなどはGitで管理しません。Gitにはこの説明と、空の標準構造を保つ
`.gitkeep`だけを登録します。

想定するサブディレクトリは次のとおりです。

- `broute/`: Bルート／スマートメータの計測データ
- `sensors/`: 温湿度など、その他のセンサ計測データ
- `database/`: SQLiteなどのローカルデータベース（保存方式は未決定）
- `exports/`: 外部利用や退避のために生成したエクスポート
- `processed/`: `data-transformer`がJSONLから生成する日付パーティション済みParquet（`broute_power`、`broute_cumulative_energy`、`broute_interval_energy`、`sen66`）
- `errors/transform/`: 変換時に検出した不正・未知トピックのJSONL（`status`は意図的に除外し、一次データは変更しない）

既存コンポーネントが使用する`broute-meter/`も移行期間中はそのまま保持します。
Docker Composeからは、原則としてOMKルート基準の`./data/...`をホスト側パスとして
参照してください。`/opt/omk/data`や`/var/lib/omk`は使用しません。

データの保持期間、削除、バックアップ、復元は、運用開始前に別途ルールを定めて
ください。ログは現在もOMKルートの`logs/`へ分離していますが、将来の運用要件に
応じて、ログと計測データそれぞれの保存先や保持方式をさらに分離する可能性があります。
