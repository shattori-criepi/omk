# OMK Dashboard

Raspberry Pi上でDockerコンテナとして動作する、利用者向けのDashboardです。7インチの
SmartiPi Touch Pro 3を横向きで使用するChromiumキオスク表示を主な利用形態とします。
瞬時値は最新状態JSON、日計電力量はDuckDBで読むParquetから取得します。

歯車アイコンから開く「管理メニュー」には、センサ管理、Bルート設定、
OMKアクセスポイント参照、システム操作があります。

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

Composeでは、`data/latest`と`data/processed`だけを読み取り専用でmountします。
認証情報ファイルやホストのsystemdソケットをDashboardコンテナへ渡しません。

## 管理メニュー

### センサ管理

`/admin/sensors`では、ホスト上の`omk-ble-sensor-manager.service`をDashboardバックエンド
経由で利用します。Dockerコンテナは`host.docker.internal:8787`へ接続し、BlueZのD-Bus
ソケットやBluetooth権限を持ちません。

周辺のペアリング不要BLE advertisementを探索し、SwitchBot等の対応センサを候補として
表示します。選択した候補に論理`device_id`、表示名、設置場所を設定して登録できます。
対応するセンサ種別や登録後の扱いは[BLE Sensor Manager README](../ble-sensor-manager/README.md)
を参照してください。

### Bルート設定

`/admin/broute`では、BルートIDとパスワードを設定し、接続状態を確認できます。保存済みの
パスワードおよび生のBルートIDは、画面やDashboard APIへ返しません。

- IDは32文字、パスワードは12文字のASCII値を扱い、入力時は4文字ごとに区切って表示します。
- 7インチ画面向けの専用ソフトウェアキーボードと、接続済み物理キーボードの両方を使用できます。
- 通常画面のパスワード入力欄はマスクし、キーボードオーバーレイではその場で入力した値だけを
  確認用に表示します。
- `starting`、`adapter_missing`、`adapter_initializing`、`scanning`、`authenticating`、
  `connected`、`scan_error`、`authentication_error`、`connection_error`、`retry_wait`、
  `status_unavailable`、`stopped`を画面上の状態として扱います。
- 未検出時は自動再試行し、長時間未検出で待機中の場合だけ「今すぐ再試行」を表示します。

RS-WSUHA-PのUSB抜去は`adapter_missing`として表示します。再挿入後はready状態を待ってから
初期化、scan、PANA接続へ自動復帰します。必要時のUSB resetはBルートサービス側に限定され、
DashboardからUSBやsystemdを直接操作しません。

Dashboardバックエンドは、`host.docker.internal:8788`のsystem-managerへBearer token付きで
要求を中継します。`scripts/setup-system-manager.sh`はroot-onlyのsystem-manager tokenから
`/etc/omk/dashboard-system-manager.env`を作成し、Composeの`env_file`でDashboardプロセスに
だけ環境変数として渡します。tokenはHTML、JavaScript、ブラウザAPIへ渡されません。このファイルは
`root:<OMKユーザーの主グループ>`、mode `0640`です。詳細は
[System Manager README](../system-manager/README.md)を参照してください。

### システム操作

`/admin/system`では、再起動とシャットダウンを実行できます。どちらも確認ダイアログを経由し、
シャットダウンは危険操作として視覚的に区別します。Dashboardは操作要求をsystem-managerへ
中継するだけで、直接`sudo`や`systemctl`を実行しません。

再起動後はDashboardが復帰をpollし、復帰したら`/display`へ戻ります。シャットダウン後は
電源を再投入するまで自動復帰しません。

### OMKアクセスポイント参照

`/admin/access-point`は、NetworkManagerの`omk-ap`プロファイルを正としてSSIDを表示します。
パスワードとWi-Fi QRコードは初期HTMLやstatus APIに含めず、「表示」操作後だけ、Dashboard
バックエンドが既存system-managerのBearer token経路でNetworkManagerから都度読み取ります。
QRコードは`WIFI:T:WPA;S:<SSID>;P:<PASSWORD>;;`形式で、Wi-Fi QR仕様の特殊文字escapeを行います。

パスワードを返す管理APIを外部インターフェースへ公開しないため、ComposeではDashboardを
`127.0.0.1:8000`（キオスク）および`192.168.50.1:8000`（OMK AP）だけへbindします。`wwan0`や
EthernetからはDashboard管理画面へ到達できません。

## Raspberry Pi Chromiumキオスク

DashboardサーバーはDocker Compose、表示用Chromiumは別のuser systemdサービスです。GUI自動
ログイン済みのWaylandセッションで、リポジトリルートから次を実行します。

```bash
./scripts/setup-dashboard-kiosk.sh
```

このサービスはDashboardのhealth応答を待ってからChromiumを起動し、Chromium終了時は5秒後に
自動再起動します。セットアップは`wtype`を確認・導入し、labwcの`HideCursor`/`WarpCursor`
キーバインドを既存のタッチ設定を残して`~/.config/labwc/rc.xml`へ追加します。詳細は
[Raspberry Pi初期セットアップ](../../docs/raspberry-pi-setup.md#dashboard-chromiumキオスク)
を参照してください。

## URL

- 表示画面: http://localhost:8000/display
- 表示更新API: http://localhost:8000/api/display
- ヘルスチェック: http://localhost:8000/health
- 管理メニュー: http://localhost:8000/admin
- Bルート設定: http://localhost:8000/admin/broute
- システム操作: http://localhost:8000/admin/system
- OMKアクセスポイント参照: http://localhost:8000/admin/access-point

Raspberry Pi自身またはLAN内から利用する場合は、`localhost`をPiのIPアドレスに置き換えます。

## データ

`OMK_LATEST_DATA_ROOT`（既定: `data/latest`）はcollectorが原子的に置換する瞬時値JSONの
ルートです。現行の固定画面は`broute_power.json`、`sen66.json`、`ichijo_power_flow.json`を
使用します。collectorは将来の可変Dashboard用に、汎用latest item store
（`items/<stable-item-id>.json`）と候補一覧（`catalog.json`）も保存しますが、Display Item選択、
プリセット、可変カード表示はまだ実装していません。
`OMK_PROCESSED_DATA_ROOT`は日計電力量用の処理済みParquetのルートです。一条`power-flow`が
10分を超えて古い場合、画面はBルート電力へフォールバックします。

データセットまたは値がない場合も画面は表示され、数値は`--`、電力状態は「データなし」、
鮮度は`unavailable`になります。日計電力量は該当データがない場合`0.0 kWh`です。

## 表示設定（Phase 2）

管理メニューの「表示設定」では、Phase 1の`catalog.json`と`items/`から検出したDisplay Itemを、
同一sourceのDisplay Blockへまとめて標準プリセットへ選択できます。blockごとに主表示、補助表示、
選択順、`large`、`medium`、`small`の三段階サイズ、表示形式（`hero`／`strip`／`compact`）を指定し、配置座標は指定しません。`hero`は左に主表示・右に補助値の縦一覧、`strip`は複数値の横長一覧、`compact`は値を同程度の大きさで省スペースにまとめます。block内ラベルはsource名を繰り返さない短縮名で表示します。太陽光・蓄電池sourceの充電・放電は、raw値を変更せずDashboard候補では1つの仮想Display Item「蓄電池充放電」として扱います。既存設定の充電／放電選択は読込み時にこの仮想itemへ移行します。標準プリセットは
容量6で、large=3、medium=2、small=1をblock単位で消費します。既存のitem単位version 1設定は
初回読込み時にsourceごとのversion 2 block設定へ自動移行します。block内の最大項目数はsizeと表示形式の組合せで決まり、largeは5〜6、mediumは5、smallは3です。

設定はホストの`data/dashboard/settings.json`へatomic replaceで保存し、Dashboardコンテナだけが
このディレクトリを読み書きします。設定ファイルは秘密情報を含まないため`0644`で保存し、通常の
ホスト運用ユーザーも読取り・バックアップできます。generic catalogが存在する初回起動時は、Bルートまたは電力値を
large、続く環境値をsmallとして容量内で初期選択します。catalogは通信断で消えないため、選択済み
blockも消えず、遅延時は最後の値、取得不可時は`--`を表示します。

`status`などの低価値な内部項目はcollectorに残したまま、Dashboardのmetric definitionで通常の
選択候補から隠します。時計、詳細、電力専用プリセット、複数ページ、自由配置、グラフは未実装です。

## 実機確認済みの管理機能

Raspberry Pi実機で、BルートID/PASS設定、正常接続、未検出時のretry表示と手動retry、
RS-WSUHA-P抜去・再挿入後の自動復旧、Dashboardの状態更新、ソフトウェアキーボード、
物理キーボード入力、Raspberry Pi再起動後のDashboard自動復帰、およびシャットダウンを確認済みです。

## テスト

```bash
cd services/dashboard
pytest -q
```

Docker Composeを使う場合:

```bash
docker compose run --rm dashboard pytest -q
```

イメージは`PYTHONPATH=/app`を設定しているため、このコマンドはコンテナ内の`/app/tests`から
`app` packageを一貫してimportします。

Composeの認証情報mount契約はリポジトリ全体を対象とするため、Dashboard image内のpytestには
含めません。リポジトリルートで次を実行します。

```bash
bash ./scripts/tests/test_dashboard_compose_config.sh
```
