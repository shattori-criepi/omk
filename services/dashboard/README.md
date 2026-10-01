# OMK Dashboard

Raspberry Pi上でDockerコンテナとして動作する、利用者向けのDashboardです。OMK Wi-Fiアクセスポイント（AP）へ接続したPCやタブレット、またはRaspberry Piへ直接接続したディスプレイから利用できます。推奨ディスプレイは7インチのRaspberry Pi Touch Display 2で、横向きのChromiumキオスク表示を想定しています。SmartiPi Touch Pro 3は組み込み用のケースです。瞬時値は最新状態JSON、日計電力量はDuckDBで読むParquetから取得します。

歯車アイコンから開く「管理メニュー」には、機器管理、Bルート設定、システム操作、OMKアクセスポイント参照、表示設定、データ書き出しがあります。

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

Composeでは、`data/processed`と`data/latest`を読み取り専用でmountし、表示設定の保存先である`data/dashboard`をDashboardコンテナへ読み書き可能な状態でmountします。system-managerとの通信に必要な`/etc/omk/dashboard-system-manager.env`は`env_file`としてDashboardプロセスへ渡します。認証情報ファイルの内容、ホストのsystemdソケット、BluetoothのD-Busソケットはコンテナへmountしません。

## 管理メニュー

### 機器管理

`/admin/sensors`では、ホスト上の`omk-ble-sensor-manager.service`をDashboardバックエンド経由で利用します。Dockerコンテナは`host.docker.internal:8787`へ接続し、BlueZのD-Bus ソケットやBluetooth権限を持ちません。

周辺のペアリング不要BLEアドバタイズを探索し、SwitchBot等の対応センサを候補として表示します。選択した候補にOMKセンサID、表示名、設置場所を設定して登録できます。対応するセンサ種別や登録後の扱いは[BLE Sensor Manager README](../ble-sensor-manager/README.md)を参照してください。

「機器管理」のOMK Nodeカードから「OMK Nodeをセットアップ」を実行できます。毎回prebuilt firmwareを書き込み、USB provisioning v2で現在のOMK AP設定を渡します。新品候補は実物確認が必要です。非同期jobの進捗は画面再読込み後も確認できます。Wi-Fi PSK、system-manager token、raw subprocess出力はブラウザへ返しません。

### Bルート設定

`/admin/broute`では、BルートIDとパスワードを設定し、接続状態を確認できます。保存済みのパスワードおよび生のBルートIDは、画面やDashboard APIへ返しません。

- IDは32文字、パスワードは12文字のASCII値を扱い、入力時は4文字ごとに区切って表示します。
- 7インチ画面向けの専用ソフトウェアキーボードと、接続済み物理キーボードの両方を使用できます。
- 通常画面のパスワード入力欄はマスクし、キーボードオーバーレイではその場で入力した値だけを確認用に表示します。
- `starting`、`adapter_missing`、`adapter_initializing`、`scanning`、`authenticating`、 `connected`、`scan_error`、`authentication_error`、`connection_error`、`retry_wait`、`status_unavailable`、`stopped`を画面上の状態として扱います。
- 未検出時は自動再試行し、長時間未検出で待機中の場合だけ「今すぐ再試行」を表示します。

RS-WSUHA-PのUSB抜去は`adapter_missing`として表示します。再挿入後はready状態を待ってから初期化、scan、PANA接続へ自動復帰します。必要時のUSB resetはBルートサービス側に限定され、DashboardからUSBやsystemdを直接操作しません。

Dashboardバックエンドは、`host.docker.internal:8788`のsystem-managerへBearer token付きで要求を中継します。`scripts/setup-system-manager.sh`はroot-onlyのsystem-manager tokenから`/etc/omk/dashboard-system-manager.env`を作成し、Composeの`env_file`でDashboardプロセスにだけ環境変数として渡します。tokenはHTML、JavaScript、ブラウザAPIへ渡されません。このファイルは`root:<OMKユーザーの主グループ>`、mode `0640`です。詳細は[System Manager README](../system-manager/README.md)を参照してください。

### システム操作

`/admin/system`では、再起動とシャットダウンを実行できます。どちらも確認ダイアログを経由し、シャットダウンは危険操作として視覚的に区別します。Dashboardは操作要求をsystem-managerへ中継するだけで、直接`sudo`や`systemctl`を実行しません。

再起動後はDashboardが復帰をpollし、復帰したら`/display`へ戻ります。シャットダウン後は電源を再投入するまで自動復帰しません。

### OMKアクセスポイント参照

`/admin/access-point`は、NetworkManagerの`omk-ap`プロファイルを正としてSSIDを表示します。パスワードとWi-Fi QRコードは初期HTMLやstatus APIに含めず、「表示」操作後だけ、Dashboard バックエンドが既存system-managerのBearer token経路でNetworkManagerから都度読み取ります。QRコードは`WIFI:T:WPA;S:<SSID>;P:<PASSWORD>;;`形式で、Wi-Fi QR仕様の特殊文字escapeを行います。

パスワードを返す管理APIを外部インターフェースへ公開しないため、ComposeではDashboardを`127.0.0.1:8000`だけへbindします。OMK AP向けの`192.168.50.1:8000`は、`wlan0`へbindしたsystemd socket proxyがloopback backendへ中継します。`wwan0`やEthernetからはDashboard管理画面へ到達できません。

## Raspberry Pi Chromiumキオスク

DashboardサーバーはDocker Compose、表示用Chromiumは別のuser systemdサービスです。GUI自動ログイン済みのWaylandセッションで、リポジトリルートから次を実行します。

```bash
./scripts/setup-dashboard-kiosk.sh
```

このサービスはDashboardのhealth応答を待ってからChromiumを起動し、Chromium終了時は5秒後に自動再起動します。セットアップは`wtype`を確認・導入し、labwcの`HideCursor`/`WarpCursor`キーバインドを既存のタッチ設定を残して`~/.config/labwc/rc.xml`へ追加します。詳細は[Gatewayの保守](../../docs/user/gateway-maintenance.md)を参照してください。

## URL

- 表示画面: http://localhost:8000/display
- 表示更新API: http://localhost:8000/api/display
- ヘルスチェック: http://localhost:8000/health
- 管理メニュー: http://localhost:8000/admin
- Bルート設定: http://localhost:8000/admin/broute
- システム操作: http://localhost:8000/admin/system
- OMKアクセスポイント参照: http://localhost:8000/admin/access-point

Raspberry Pi自身では上記の`localhost`を使用します。OMK APに接続した端末では、`localhost`を`192.168.50.1`へ置き換えます。たとえば表示画面は`http://192.168.50.1:8000/display`です。DashboardはEthernet、`wwan0`、家庭・研究所などの外部ネットワークへ公開しません。

## データ

`OMK_LATEST_DATA_ROOT`（既定: `data/latest`）はcollectorが原子的に置換する瞬時値JSON、Display Item catalog（`catalog.json`）、各item（`items/<stable-item-id>.json`）のルートです。通常の現行表示では、catalogから検出したDisplay ItemをDisplay Blockへまとめて表示します。`OMK_PROCESSED_DATA_ROOT`は日計電力量用の処理済みParquetのルートです。住宅用PV・蓄電池・PCS profileの`power-flow`が10分を超えて古い場合、画面はBルート電力へフォールバックします。

generic catalogまたは表示設定が利用できない場合は、互換の固定表示へフォールバックします。この表示は`broute_power.json`、`sen66.json`、`ichijo_power_flow.json`を使いますが、通常の現行表示を置き換えるものではありません。

データセットまたは値がない場合も画面は表示され、数値は`--`、電力状態は「データなし」、鮮度は`unavailable`になります。日計電力量も該当データがない場合は`--`で、実測ゼロと区別します。日計は`broute_interval_energy`の`end_at`がJST当日となる買電量・売電量の合計です。transformerが生成済みのParquetだけを読むため、latestの瞬時値とは更新時期が異なります。

## 表示設定

管理メニューの「表示設定」では、catalogと`items/`から検出したDisplay Itemを、同一sourceのDisplay Blockへまとめて選択できます。画面では「おすすめ」「カスタム」「時計」「デモ」の4つを選べます。おすすめは現在の候補から選択可能かつ鮮度が`normal`または`delayed`の主要項目を表示のたびに自動選択します。後から届いたセンサも手動更新なしで反映し、`unavailable`は除外します。settings v3のおすすめ保存領域と更新APIは互換性のため残しますが、通常表示と管理画面のおすすめ一覧は保存内容に依存しません。カスタムでは表示するデータ、主表示、順序、サイズ、表示形式を設定します。時計は日付・時刻を大きく表示し、おすすめと同じ選択可能・鮮度条件で現在の電力・環境情報を自動選択します。センサがなくても時計は表示し、後から届いた項目や構成の変化はポーリングで自動反映します。settings v3のclock item_idsは互換性のため保持しますが、表示時には参照しません。blockには`large`、`medium`、`small`の三段階サイズと、`hero`、`strip`、`compact`の表示形式があります。配置座標、複数ページ、自由配置、グラフは実装していません。

設定はホストの`data/dashboard/settings.json`へatomic replaceで保存し、Dashboardコンテナだけがこのディレクトリを読み書きします。設定ファイルは秘密情報を含まないため`0644`で保存し、通常のホスト運用ユーザーも読取り・バックアップできます。初回起動時はおすすめを初期選択します。カスタム設定ではcatalogは通信断で消えないため、選択済みblockも消えず、遅延時は最後の値、取得不可時は`--`を表示します。

smallサイズの複数項目は、表示形式（hero / strip / compact）にかかわらず1列に縦積みします。各縦行を左側のラベル・バッジと右側の値・単位に分け、カードの横幅を使って表示します。長いラベル・単位は折り返し、通常表示にも同じルールを適用します。smallの値・ラベル・単位は項目数（1・2・3）とカード寸法に応じて拡大し、2・3項目は各段を均等に利用します。medium / largeは従来の表示を維持します。

`status`などの低価値な内部項目はcollectorに残したまま、Dashboardのmetric definitionで通常の選択候補から隠します。

### デモモード

「デモ」を選ぶと、既存の`mode`値を増やさずに`demo.enabled`を有効化し、デモcustom固定配置から開始します。通常の「おすすめ」「カスタム」「時計」を選ぶと`demo.enabled`を無効化して選択した`mode`へ戻ります。デモを有効にすると、正常・遅延の実値を優先し、古い値・取得不可の値を匿名化fixtureで補います。各DisplayItemとAPIの`source_kind`（`real` / `demo`）で値の由来を識別し、デモON時だけ各値に小さな「実測」「模擬」バッジを表示します。通常表示ではバッジを表示しません。おすすめ・時計では、保存済みの構成とは別に、実候補と不足するBルート・環境・パワコンの一時候補から毎回構成を生成するため、登録済みセンサ・latest・Parquetがない新品Gatewayでも表示できます。環境項目は既存のおすすめと同じ順位のグループを補完し、他グループに利用可能な実値があればその値を優先します。

デモ中のカスタムは、保存済みcustomに依存しない展示用固定配置です。largeはパワコン（負荷・PV・買電・売電・蓄電池SOC・充放電）、mediumは室内環境（温度・湿度・CO₂・PM2.5・VOC・NOx）、smallは外気温・外気相対湿度を表示し、Bルートblockは追加しません。mediumの6項目はデモ専用で、通常設定の項目数上限は変更しません。一時候補は表示応答の中だけに存在し、catalog、latest、Parquet、機器登録、通常設定・カスタム設定には保存されません。デモを無効にすると保存済みの通常表示に戻ります。

デモcustomの外気温・外気相対湿度は常に固定fixture（25.1 ℃・50 %）を使い、`source_kind="demo"`として「模擬」バッジを表示します。外気用の実測センサ探索やBLE登録情報の参照は行わず、sensor-managerの稼働状態にも依存しません。

## 実機確認済みの管理機能

Raspberry Pi実機で、BルートID/PASS設定、正常接続、未検出時のretry表示と手動retry、RS-WSUHA-P抜去・再挿入後の自動復旧、Dashboardの状態更新、ソフトウェアキーボード、物理キーボード入力、Raspberry Pi再起動後のDashboard自動復帰、およびシャットダウンを確認済みです。

## テスト

```bash
cd services/dashboard
pytest -q
```

Docker Composeを使う場合:

```bash
docker compose run --rm dashboard pytest -q
```

イメージは`PYTHONPATH=/app`を設定しているため、このコマンドはコンテナ内の`/app/tests`から`app` packageを一貫してimportします。

Composeの認証情報mount契約はリポジトリ全体を対象とするため、Dashboard image内のpytestには含めません。リポジトリルートで次を実行します。

```bash
bash ./scripts/tests/test_dashboard_compose_config.sh
```
