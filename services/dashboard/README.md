# OMK Dashboard

Raspberry Pi Touch Display 2（横向き）のChromiumキオスクで表示する、利用者向けの最小ダッシュボードです。DuckDBから読み取り専用でParquet実データを取得します。

## ローカル起動

Python 3.12で仮想環境を作成し、依存関係を入れて起動します。

```bash
cd services/dashboard
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
OMK_PROCESSED_DATA_ROOT=../../data/processed uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Docker Composeでの起動

リポジトリルートで実行します。

```bash
docker compose up --build dashboard
```

## URL

- 表示画面: http://localhost:8000/display
- ヘルスチェック: http://localhost:8000/health

Raspberry Pi自身またはLAN内からは、`localhost` をPiのIPアドレスに置き換えてください。

## データ

`OMK_PROCESSED_DATA_ROOT` で処理済みParquetのルートを指定します。未指定時はローカル起動向けに `data/processed` を使用します。Composeでは `./data/processed` を `/app/data/processed` へ読み取り専用でマウントします。

データセットまたは値がない場合も画面は表示され、数値は `--`、電力状態は「データなし」、鮮度は `unavailable` になります。日計電力量は該当データがない場合 `0.0 kWh` です。

## テスト

```bash
cd services/dashboard
pytest -q
```

Docker Composeを使う場合:

```bash
docker compose run --rm dashboard pytest -q
```

## 現状と未実装事項

DuckDB／Parquet実データ接続済みです。MQTT購読、WebSocket、管理者画面、自動更新、認証、グラフ、自動起動設定、systemdは未実装です。
