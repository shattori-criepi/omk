# Bルート・スマートメータ

RS-WSUHA-Pを介して低圧スマート電力量メーターから瞬時電力と定時積算電力量を取得し、30分ごとの買電量・売電量を保存するPythonアプリケーションです。

このディレクトリはOMKリポジトリ内の独立したPythonサブプロジェクトです。Windows 11上のUbuntu（WSL2）での開発・実機確認と、64-bit Raspberry Pi OSを搭載したRaspberry Pi 4（正式な動作確認対象）での運用に、同じソースコードを使用します。環境差はシリアルポート等の設定で吸収します。

## 実装状況

このサービスは、Bルート認証、PANA接続、ECHONET Liteの計測値取得、定期保存、通信失敗時の再試行と再接続を実装しています。

`run`、`setup-adapter`、`test-connection`は実装済みです。`test-connection`は実際にPANA接続し、E7、D3、D7、E1、EA、EBを取得します。D3が非搭載の場合は仕様に従って係数1、EBが非搭載の場合は逆方向非対応として扱います。

## 対応環境とハードウェア

- Python 3.12以降
- Windows 11
- Raspberry Pi OS 64-bit（Raspberry Pi 4が正式な動作確認対象）
- Bルート対応USBドングル RS-WSUHA-P
- Bルート対応低圧スマート電力量メーター

通常の単体テストにRS-WSUHA-Pやスマートメーターは不要です。

## セットアップ

### Ubuntu（WSL2）

Windows 11上のUbuntuターミナルで次を実行します。PythonアプリケーションはWindows側ではなくUbuntu側で動かします。

```bash
cd services/broute-meter
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp config/settings.example.yaml config/settings.yaml
cp config/credentials.example.yaml config/credentials.yaml
```

### Raspberry Pi OS

```bash
cd services/broute-meter
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp config/settings.example.yaml config/settings.yaml
cp config/credentials.example.yaml config/credentials.yaml
```

`config/credentials.yaml`、`config/settings.yaml`、`.env`はGit管理対象外です。リポジトリへ登録するのはダミー値を含む`*.example.yaml`だけです。

## 実行環境

GatewayではRaspberry Piホストのsystemdサービスとして実行します。Windowsで開発する場合は、RS-WSUHA-PをUbuntu（WSL2）へUSBパススルーし、Ubuntu上でPythonを直接実行できます。

Docker構成はモック専用です。USBデバイス、特権モード、Bルート認証情報をコンテナへ渡しません。Docker環境の判定やデバイス割当て処理もPythonコードへ入れていません。

## 設定

一般設定は`config/settings.yaml`、Bルート認証情報は`config/credentials.yaml`へ分離します。設定ファイルを省略した項目にはアプリケーション内の既定値が使われます。

```yaml
# config/settings.yaml
measurement:
  instantaneous_interval_seconds: 10
  # EA/EBは起動時と、毎時00分・30分の後にだけ取得する。
  cumulative_fetch_delay_seconds: 5

serial:
  port: null
  baudrate: 115200
  timeout_seconds: 5

adapter:
  auto_configure: true
  expected_settings:
    uart_mode: "80"
    output_mode: "01"

retry:
  request_timeout_seconds: 5
  request_max_attempts: 3
  reconnect_after_consecutive_failures: 5
  reconnect_wait_seconds: 30

storage:
  # OMKルート直下の共通データ領域（このREADMEのコマンドはservices/broute-meterから実行）。
  data_directory: "../../data/broute-meter"

logging:
  level: "INFO"
  directory: "../../logs/broute-meter"

# MQTTは取得済みデータを汎用sensor-collectorへ配送する任意の経路です。
mqtt:
  enabled: true
  host: "127.0.0.1"
  port: 1883
  device_id: "broute-001"
  topic_prefix: "omk"
  client_id: "omk-broute-001"
```

`cumulative_fetch_delay_seconds`は30分境界後の待機秒数です。既存の`cumulative_check_interval_seconds`は設定読込みの互換性のためだけに受け付け、`run`のEA/EB取得周期には使用しません。

`run`では、アクティブスキャンで候補が見つからない、またはPANA接続に失敗した場合でも終了しません。`retry.reconnect_wait_seconds`待機後に、Bルート認証からスキャン、PANA接続までを再試行します。`Ctrl+C`または`SIGTERM`で待機中の再試行を安全に中断できます。診断用の`test-connection`は単発実行のため、失敗時に再試行を継続せず終了します。

```yaml
# config/credentials.yaml
b_route:
  id: "YOUR_B_ROUTE_ID"
  password: "YOUR_B_ROUTE_PASSWORD"
```

優先順位は「環境変数 > YAML > アプリケーション内既定値」です。次の環境変数に対応します。

- `B_ROUTE_ID`
- `B_ROUTE_PASSWORD`
- `B_ROUTE_SERIAL_PORT`
- `B_ROUTE_INSTANT_INTERVAL`
- `B_ROUTE_CUMULATIVE_INTERVAL`
- `B_ROUTE_CUMULATIVE_DELAY`
- `B_ROUTE_DATA_DIR`
- `B_ROUTE_LOG_LEVEL`
- `OMK_DATA_DIR`
- `OMK_LOG_DIR`
- `MQTT_ENABLED`, `MQTT_HOST`, `MQTT_PORT`, `MQTT_DEVICE_ID`, `MQTT_TOPIC_PREFIX`, `MQTT_CLIENT_ID`
- `MQTT_USERNAME`, `MQTT_PASSWORD`（認証を使う場合は両方を指定）

`OMK_DATA_DIR`と`OMK_LOG_DIR`は、OMK共通の実行時保存先を指定するための環境変数です。両方が指定されている場合は、互換用の`B_ROUTE_DATA_DIR`より`OMK_DATA_DIR`を優先します。開発時の既定値はOMKルートから見て`data/broute-meter/`と`logs/broute-meter/`です。実行時データ・ログはGit管理対象外です。

瞬時電力間隔は10秒以上でなければなりません。空の環境変数は下位設定へフォールバックせず、設定ミスとして扱います。BルートIDまたはパスワードが不足している場合、通信を開始するコマンドは設定エラーで終了します。

認証情報をコマンドライン引数へ直接書くことは避けてください。シェル履歴やプロセス一覧へ残る可能性があります。`check-config`はパスワードを表示せず、BルートIDも一部だけを表示します。

## MQTT配送

Bルート通信はRaspberry Piホスト上のsystemdサービスで直接実行し、コンテナ化しません。取得済みの計測モデルは専用CSVへ保存した後、MQTTへもbest-effortでpublishされます。MQTT経由のデータは汎用`sensor-collector`がJSONLへ一次保存します。CSV保存は現時点では維持し、MQTT経路が安定した後に分析・受け渡し用の後段生成へ移行するかを判断します。

トピックは`{topic_prefix}/{device_id}/power`、`cumulative-energy`、`interval-energy`、`status`です。測定値はQoS 0・retainなし、statusはretainありです。MQTT切断中の測定値はキューやディスクへ蓄積せず再送もしません。MQTTの接続・publish障害はBルート計測、30分値計算、CSV保存を停止させません。

## シリアルポートの確認

利用可能なポートと、取得可能なUSB機器情報を表示します。

```bash
python -m broute_meter list-ports
```

Windowsではデバイスマネージャーの「ポート（COMとLPT）」で、ホスト側の`COM3`等を確認できます。WSL2で実行する場合、アプリケーションへ`COM3`を指定せず、usbipd-winでUSB機器自体をUbuntuへアタッチします。usbipd-winが未導入の場合は、管理者PowerShellで`winget install --exact --id dorssel.usbipd-win`を実行します。

```powershell
# 管理者PowerShell。BUSIDは usbipd list の結果へ置き換える。
usbipd bind --busid 8-1

# 以後は通常PowerShell。WSL再起動やUSB再接続後に再実行する。
usbipd attach --wsl --busid 8-1
```

アタッチ後、Ubuntu側でポートを確認します。Raspberry Pi OSでも同じ確認方法を使用します。永続名が利用できる場合は`/dev/serial/by-id/...`を推奨します。`dialout`グループ等の権限も確認してください。

```bash
python -m broute_meter list-ports
sudo usermod -aG dialout "$USER"  # 初回だけ。新しいログインセッションで反映
export B_ROUTE_SERIAL_PORT=/dev/serial/by-id/...
python -m broute_meter check-config
```

明示指定がない場合、USB機器情報の製品名に`RS-WSUHA-P`を含むポートだけを候補にします。ただし、確認済み実機は`FT230X Basic UART`（`0403:6015`）として見え、この条件では自動検出できません。このdescriptorは他のFT230Xと区別できないため、VID/PIDだけでは選択しません。`serial.port`に実際の`/dev/serial/by-id/...`を明示する方法を正式なfallbackとします（systemd用には環境変数だけでなく`config/settings.yaml`へ保存）。候補0件・複数件・確証不足では推測で選択しません。

## CLI

```bash
python -m broute_meter run
python -m broute_meter list-ports
python -m broute_meter check-config
python -m broute_meter test-connection
python -m broute_meter setup-adapter
```

別の設定ファイルを検査する場合は、サブコマンドより前にパスを指定します。

```bash
python -m broute_meter \
  --settings config/settings.yaml \
  --credentials config/credentials.yaml \
  check-config
```

`list-ports`と`setup-adapter`はBルート認証情報を読み込みません。`check-config`は通信を行わず、最終的に採用された設定だけを安全な形式で表示します。

`setup-adapter`は起動ごとに現在値を読み、期待値と異なる場合だけ書き込み、書込み後に再読出しします。

```bash
python -m broute_meter setup-adapter
```

既に一致している場合はFLASHへ書き込みません。`adapter.auto_configure: false`で不一致の場合も書き込まず、設定が未完了であることを示す終了コード`5`を返します。確認済みのコマンド、FLASH書込み制限、推測実装していない事項は[RS-WSUHA-P設定処理の根拠と制約](docs/rs-wsuha-p-settings.md)を参照してください。

Ubuntu（WSL2）でスマートメーターまでの接続と瞬時・積算電力量を確認する場合は、認証情報を設定し、アタッチ後に表示されたLinuxデバイス名を指定して次を実行します。

```bash
export B_ROUTE_SERIAL_PORT=/dev/serial/by-id/...
python -m broute_meter check-config
python -m broute_meter test-connection
```

`test-connection`はアダプター設定を確認し、Bルート認証、スキャン、PANA接続、ECHONET Lite Get要求を順に実行します。成功時はスマートメーターIPv6アドレス、タイムゾーン付き計測時刻、`net_power_w`、正方向・逆方向積算電力量を表示して終了します。BルートIDとパスワードは表示しません。

実機やシリアルポートを使わず、同じ設定確認フローを`MockAdapter`で実行することもできます。

```bash
python -m broute_meter \
  --settings config/settings.example.yaml \
  setup-adapter --mock
```

## Dockerによるモック実行

DockerfileはこのPythonサブプロジェクト内にあります。B-route開発用Compose定義はリポジトリルートの`compose.dev.yaml`です。Dockerではホストの`data/broute-meter/`と`logs/broute-meter/`を、コンテナの`/data`と`/logs`へマウントします。

```bash
cd ../..
docker compose -f compose.dev.yaml up --build broute-meter-mock \
  --abort-on-container-exit \
  --exit-code-from broute-meter-mock
```

既定サービスはランタイムイメージで`setup-adapter --mock`を実行して終了します。USBドングルや認証情報は不要です。

Docker内でモック統合テストを実行する場合は、`test`プロファイルを使用します。

```bash
docker compose -f compose.dev.yaml --profile test run --build --rm broute-meter-tests
```

ランタイムイメージとテストイメージは同じ[Dockerfile](Dockerfile)の別ステージです。通常の直接実行・直接テストは、Dockerをインストールしていない環境でも従来どおり利用できます。

## ログ

標準エラーと`logging.directory`配下の`broute-meter.log`へ出力します。ファイルは日次でローテーションします。ログレベルは`DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`から選べます。

Bルートパスワード、BルートID全文、認証情報ファイル内容は出力しません。デバッグ電文でも、認証コマンドは必ずマスクします。

## テスト

```bash
python -m pytest
python -m pytest --cov=broute_meter --cov-report=term-missing
python -m ruff check .
```

テストはYAMLと環境変数の優先順位、入力検証、秘密値マスク、ログローテーション、ポート情報変換、シリアル通信、分割・複数行応答、差分設定、Bルート接続シーケンス、ECHONET Lite電文、TID照合、E7の正・負・ゼロ、D3・D7・E1・EA・EBの解析と換算、時刻基準スケジュール、CSV切替・重複防止、30分差分と品質判定、要求再試行・連続失敗後の再接続、CLI出力をモックで確認し、実機へ接続しません。

通常のテストはDockerを経由しません。Docker設定自体もPyYAMLによるテストで検査するため、Docker Engineがない開発環境で回帰テストを実行できます。

## 計測値とCSV

瞬時電力`net_power_w`は系統接続点の正味電力です。

- 正: 系統から住宅への買電
- 負: 住宅から系統への逆潮流
- 0: 系統との正味電力交換なし

これは住宅内の総消費電力ではありません。太陽光発電がある住宅の総消費量をBルート値だけから求めることはできません。正方向積算値の差分を`import_energy_kwh`（買電量）、逆方向積算値の差分を`export_energy_kwh`（売電量）として扱います。

CSVはUTF-8、ISO 8601のタイムゾーン付き日時を使用します。

- `instantaneous_power_YYYYMMDD.csv`: `measured_at,net_power_w`
- `cumulative_energy_YYYYMM.csv`: 計量時刻・受信時刻・正逆方向のraw値とkWh値
- `interval_energy_YYYYMM.csv`: 開始・終了時刻、30分買電量・売電量、品質状態

積算値が30分間隔でない場合、前回値がない場合、または差分が負の場合は、正常な30分値を捏造しません。欠測値を0として保存しません。

## 実機通信のトラブルシュート

通信できない場合は、次の境界を順に確認します。

1. OSがRS-WSUHA-Pを認識しているか
2. ポート名とアクセス権限が正しいか
3. BルートID・パスワードが設定されているか
4. アダプター設定確認、スキャン、PANA接続のどこで失敗したか
5. ECHONET Lite要求がタイムアウトしていないか

RS-WSUHA-Pのフラッシュ設定を起動ごとに読み出し、期待値と異なる場合だけ書き込み、再読出しで確認します。無条件の書込みやローカルフラグだけによる判定は行いません。通信要求の再試行、PANA再接続、シリアル再オープンも行います。

### 正常shutdown時の計測キャンセル

SIGINT/SIGTERMによる明示的な終了キャンセルは通信障害や欠測ではありません。`AdapterOperationCancelled`および再接続待機中の終了通知は`MeasurementCancelledError`として扱い、正常shutdownでは計測値の保存・publish・欠測WARNINGを生成しません。一方、`AdapterCommunicationError`やresponse timeoutなど実際の通信失敗は、終了要求の有無だけで無視せず、従来どおり欠測として扱います。

2026-08-21にRaspberry Pi 4 Gatewayで`omk-broute-meter.service`をrestartして確認した。restart前は10秒周期で瞬時電力を取得しており、SIGTERM受信後は`終了シグナルを受信しました signal=15`、`アプリケーションコマンド終了 command=run exit_code=0`、systemdの`Deactivated successfully`で終了した。この間に瞬時電力・定時積算電力量の取得失敗および欠測WARNINGは発生しなかった。restart後は設定読出しが`startup_attempt=1`で成功し、scan attempt 1は`candidates=0`、attempt 2は`candidates=1`、PANA接続成功後に定期計測を再開した。PANA後の最初の瞬時電力取得では実際のresponse timeoutが1回発生し、従来どおり欠測として扱われた。その後は14:44:41から14:45:31まで10秒周期で連続して瞬時電力保存へ復帰した。

### USBシリアル応答停止時の自動復旧

RS-WSUHA-PがOS上のUSBデバイスとして見えていても、`SerialTimeoutError`または確認済み形式の応答待ちタイムアウトで設定読出しに失敗することがあります。起動時の通常再試行、serial close/open後の1回のSKRESET、1プロセスにつき1回のlogical USB resetまで失敗した場合だけ、検証済みPi 4 topologyでは最終手段としてVBUS cycleを1回要求します。VBUS cycle後はtrusted serialに対応する検証済みttyの再出現を最大45秒待ち、5秒settleして設定読出しを再試行します。失敗時は`vbus_recovery_failed`および`adapter_unresponsive`として5分間隔へ移行します。`data/broute-meter/broute-recovery-state.json`には秘密情報を含めず、状態、最終logical reset時刻、最終VBUS cycle時刻を保存します。logical resetは20分、VBUS cycleは1時間抑止します。

RS-WSUHA-Pのcommand responseが停止した場合、logical USB resetだけでは復旧せず、VBUS cycleが必要だった実機例があります。Raspberry Pi 4のオンボードUSBはganged powerのため、VBUS cycleではOnyxやUSBキーボードなど他のUSB機器も一時的に切断されます。

2026-08-21の自動復旧試験では、reboot前にB-routeは`connected`/`measuring`、SORACOMはconnectedだった。Gateway reboot後、RS-WSUHA-Pの設定読出しは6回timeoutし、SKRESET後およびlogical USB reset後も設定読出しに失敗したため、自動VBUS recoveryが発火して`/usr/local/lib/omk/cycle-gateway-usb-vbus`が実行された。Pi 4のganged USB powerによりFT230X/RS-WSUHA-P、EG25-G、USB keyboardは一時disconnectした。VBUS ON後はFT230Xが`ttyUSB0`として再enumerateし、RS-WSUHA-P設定読出しに成功した。EG25-Gも再enumerateし、NetworkManager、SORACOM、Napterは自動復帰した。smart meter scanはattempt 1で`candidates=0`、attempt 2で`candidates=1`となり、PANA接続に成功した。14:00積算電力量と30分買売電量は`quality_status=normal`で保存され、瞬時電力の継続取得も復帰した。最終的にruntime stateは`connected`、recovery statusは`measuring`、`last_vbus_cycle_at`は記録済み、SORACOMもconnectedであった。人手によるB-route restart、USB抜き差し、手動VBUS操作なしで完全復旧している。これは今回のRaspberry Pi 4 + RS-WSUHA-P実機でreboot後の固着と自動復旧を確認した記録であり、Gateway rebootが一般に必ず固着を引き起こすことを意味しない。Pi 5等は未検証であり、この回復は検証済みPi 4 USB2 hub topologyに限定する。

USB操作はBルート本体ではなく、root所有・引数なしの`/usr/local/lib/omk/reset-rs-wsuha-p-usb`が行います。対象は管理者がアプリケーション層の設定通信で確認し、`/etc/omk/broute-usb-recovery.conf`へ登録したUSB個体だけです。ファイルはserial 1行＋改行、root:root・0644、`/etc/omk`はroot:root・0755とし、Git管理しません。一般ユーザー／serviceには登録・更新権限を与えず、sudoersではlogical resetとB-route専用VBUS helperの引数なし実行だけを許可します。ファイルはshellとして実行せず、所有者・権限・形式・リンクを検査します。

Pythonとhelperはそれぞれ、trusted serialとの一致、tty character deviceとcanonical sysfsの対応、USB parent、transportの追加安全条件`0403:6015`、対象の一意性を検証します。このVID/PIDはモデルを識別する公式仕様ではありません。USB製品名が`FT230X Basic UART`でも、登録済み個体なら復旧対象です。unbind/bind後は同じidentityの再出現を最大15回・1秒間隔で確認します。登録済み環境の`run`は存在確認・再open・VBUS後の待機を含めtrusted serialから現在のportを再解決します。tty番号が変われば新しいttyへ追従し、旧ttyを別FT230Xが取得しても使用しません。検証済みby-idを優先しますが、リンク生成前は検証済みcanonical ttyを使えます。

identity未登録の通常計測・通常の`setup-adapter`には登録は不要です。登録ファイルが存在する環境の`run`はtrusted identityを基準とし、不正な登録・identityの矛盾時には無検証通信へfallbackしません。登録・交換はサービスを停止して行い、登録後にサービスを起動し直してください。cooldown・reset回数上限は従来どおりです。

### USB recovery対象の初回登録（管理者）

先に通常のsetupでアプリケーションとhelperを更新してください。次はrepository rootから実行します。`config/settings.yaml`の`serial.port`に実機のby-idを明示し、他のプロセスが同じportを使用していない状態にします。

```bash
sudo systemctl stop omk-broute-meter.service
cd services/broute-meter
sudo .venv/bin/python -m broute_meter --settings config/settings.yaml \
  setup-adapter --trust-usb-recovery
sudo systemctl start omk-broute-meter.service
```

これは管理者が信頼できるOMKコード／venvを明示的にroot実行する操作です。登録コマンド自体をservice用sudoersへ追加しないでください。Bルート認証情報は読み込まず、実機の設定読出し（不一致なら既存設定に従って変更・再確認）に成功し、通信前後のUSB identityが一致した場合だけ登録します。`--mock`は禁止し、service用ログファイルも作成しません。同じ個体の再登録は変更なし、別個体への自動上書きは拒否します。交換時はserviceを停止し、管理者が既存identityを退避・削除してから新個体と正常通信して再登録してください。設定不能な個体をdescriptorだけで登録する手順はありません。

引数なしの`/usr/local/lib/omk/cycle-gateway-usb-vbus`はB-route専用です。安全なtrusted登録、直前のlogical resetが発行したroot管理の10分・1回限りの許可、root側の1時間cooldown、Pi 4・`1-1`・`2109:3431`・USB2系4-port hubを確認してから固定の`uhubctl -l 1-1 -a cycle -d 5`だけを実行し、終了・中断時もbest-effortでpower ONを試みます。後者はオンボードUSBをgangedで切断するため、他のUSB機器やLTEモデムを個別対象として扱うものではありません。USB identity検証でlogical resetを拒否した場合、VBUS cycleへは進みません。 adapterが消失していても新鮮なroot許可と矛盾のないtopologyがあれば復旧を許可し、許可なしの直接実行は拒否します。両helperとsudoersは一緒に更新してください。詳細な権限境界、各段階のidentity追跡、更新・実機確認計画は[USB recovery設計](docs/usb-recovery.md)を参照してください。

Raspberry Piでは次でsystemd unit、root所有ヘルパー、対象ヘルパーだけを許可するsudoers設定を導入します。Bルート本体は指定ユーザーで実行され、rootでは動作しません。

```bash
./scripts/setup-broute-meter.sh --dry-run
./scripts/setup-broute-meter.sh --print-unit
./scripts/setup-broute-meter.sh
sudo systemctl status omk-broute-meter.service --no-pager
sudo journalctl -u omk-broute-meter.service -n 100 --no-pager
```

通常実行のログは`logs/setup/broute-meter-setup-YYYYMMDD-HHMMSS.log`へ保存されます。再実行時は helper、sudoers、unitの内容を比較し、同一なら維持します。変更があるunitだけをdaemon-reloadし、すでにactiveなサービスは更新したapplicationを反映するためrestartし、inactiveならstartします。スクリプトは既存CSV、認証情報、`broute-recovery-state.json`、Bルートログを変更・削除しません。

`--dry-run`はsudoや書込みを実行せず、前提条件をすべて表示します。不足があれば予定を可能な範囲で表示した後、変更なしで非ゼロ終了します。root専用の既存sudoersはdry-runでは内容を読まず、通常実行時に権限付き比較で判定します。

systemdは`Restart=on-failure`、60秒待機、15分あたり5回の起動失敗上限です。上限に達して`failed`になった場合、原因を取り除いた後に次で再開します。

```bash
sudo systemctl reset-failed omk-broute-meter.service
sudo systemctl restart omk-broute-meter.service
```

自動復旧できない場合は、サービスを停止してからRS-WSUHA-Pだけを抜き差しし、by-idパスの再出現後にサービスを起動します。設定読出し、アクティブスキャン、PANA接続、瞬時電力の取得をjournalで確認してください。

```bash
sudo systemctl stop omk-broute-meter.service
# RS-WSUHA-Pを抜き差しする
ls -l /dev/serial/by-id/
sudo systemctl start omk-broute-meter.service
```

実際のBルートID・パスワードを、ソース、テスト、ログ、README、Issue、コミットへ含めないでください。

Gatewayの旧AP broker addressを`config/settings.yaml`の`mqtt.host`または`MQTT_HOST`に指定している場合は、`127.0.0.1`へ変更してからsetupを再実行してください。setupは永続設定の旧hostを検出するとservice変更前に停止し、既存設定を無断で書き換えません。標準unitはsetup shellの`MQTT_HOST`/`MQTT_PORT`を引き継がないため、checkerもそれらでYAML値を上書きしません。旧標準hostだけではOMK生成設定と利用者指定を区別できないため、自動migrationは行いません。明示した外部brokerは保持します。
