# OMK Dashboard

Raspberry Pi Touch Display 2（横向き）のChromiumキオスクで表示する、利用者向けの最小ダッシュボードです。

## ローカル起動

Python 3.12で仮想環境を作成し、依存関係を入れて起動します。

```bash
cd services/dashboard
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
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

画面は現在、Pythonのview modelから供給する仮データを表示します。DuckDB接続、Parquet読込み、MQTT購読、WebSocket、管理者画面、認証、グラフ、自動起動設定、systemdは未実装です。
