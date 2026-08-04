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

## Raspberry Pi Chromiumキオスク

dashboardサーバーはDocker Compose、表示用Chromiumは別のuser systemdサービスです。GUI自動ログイン済みのWaylandセッションで、リポジトリルートから次を実行します。

```bash
./scripts/setup-dashboard-kiosk.sh
```

このサービスはdashboardのhealth応答を待ってからChromiumを起動し、Chromium終了時は5秒後に自動再起動します。SSHやNapterの接続には依存しません。状態確認、再起動、ログ確認、停止は次のとおりです。

```bash
systemctl --user status omk-dashboard-kiosk.service --no-pager
systemctl --user restart omk-dashboard-kiosk.service
journalctl --user -u omk-dashboard-kiosk.service --no-pager
systemctl --user disable --now omk-dashboard-kiosk.service
```

Waylandセッションが起動していない場合はセットアップを実行せず、GUIへログインしてから実行してください。セットアップ時にソケットがなければ安全に停止し、GUIログイン後の再実行を求めます。GUI自動ログイン時にuser managerの`default.target`から起動し、unit内ではWaylandソケットを待ちます。lingerは不要です。SSHやNapterの切断には依存しません。

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

latest JSONとDuckDB／Parquet実データ接続済みです。画面は10秒ごとに`/api/display`から値を更新します。MQTT購読、WebSocket、管理者画面、認証、グラフは未実装です。
