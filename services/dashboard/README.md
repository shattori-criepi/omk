# OMK Dashboard

Raspberry Pi Touch Display 2（横向き）のChromiumキオスクで表示する、利用者向けの最小ダッシュボードです。瞬時値は最新状態JSON、日計電力量はDuckDBによるParquetから取得します。

## ローカル起動

Python 3.12で仮想環境を作成し、依存関係を入れて起動します。

```bash
cd services/dashboard
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
OMK_PROCESSED_DATA_ROOT=../../data/processed OMK_LATEST_DATA_ROOT=../../data/latest uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Docker Composeでの起動

リポジトリルートで実行します。

```bash
docker compose up --build dashboard
```

## URL

- 表示画面: http://localhost:8000/display
- 表示更新API: http://localhost:8000/api/display
- ヘルスチェック: http://localhost:8000/health

Raspberry Pi自身またはLAN内からは、`localhost` をPiのIPアドレスに置き換えてください。

## データ

`OMK_LATEST_DATA_ROOT`（既定: `data/latest`）はcollectorが原子的に置換する瞬時値JSONのルートです。対象は`broute_power.json`、`sen66.json`、`ichijo_power_flow.json`です。`OMK_PROCESSED_DATA_ROOT` は日計電力量用の処理済みParquetのルートです。Composeでは両方をdashboardへ読み取り専用でマウントします。一条`power-flow`が10分を超えて古い場合、画面はBルート電力へフォールバックします。

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

latest JSONとDuckDB／Parquet実データ接続済みです。画面は10秒ごとに`/api/display`から値を更新します。MQTT購読、WebSocket、管理者画面、認証、グラフ、自動起動設定、systemdは未実装です。
