# Bルート・スマートメータ

RS-WSUHA-Pを介して低圧スマート電力量メーターから瞬時電力と定時積算電力量を取得し、30分ごとの買電量・売電量を保存するPythonアプリケーションです。

このディレクトリはOMKリポジトリ内の独立したPythonサブプロジェクトです。Windows 11上のUbuntu（WSL2）での開発・実機確認と、64-bit Raspberry Pi OSを搭載したRaspberry Pi 4／5での運用に、同じソースコードを使用します。環境差はシリアルポート等の設定で吸収します。

## 実装状況

現在はPhase 7まで実装済みです。

- Phase 1: Pythonプロジェクト基盤、設定読込み・検証、秘密値マスク、ローテーションログ、CLI基盤、シリアルポート一覧
- Phase 2: シリアル通信抽象化、RS-WSUHA-Pの`RUART`／`WUART`処理、不一致時だけの設定書込みと再確認、モックアダプター、最小Docker実行環境
- Phase 3: Bルート認証設定、アクティブスキャン、IPv6アドレス解決、PANA接続、`test-connection`
- Phase 4: ECHONET Lite形式1の生成・解析、TID照合、`SKSENDTO`／`ERXUDP`、E7・D3・D7・E1・EA・EBの解析、`Decimal`による積算値換算
- Phase 5: 時刻基準の定期計測、日次・月次CSV、積算値重複防止、SIGINT／SIGTERMによる安全終了
- Phase 6: 30分買電・売電量、欠測・時刻差・負差分・逆方向非対応の品質判定
- Phase 7: 単一要求の再試行、連続失敗の追跡、シリアル再オープンとPANA再接続

`run`、`setup-adapter`、`test-connection`は実装済みです。`test-connection`は実際にPANA接続し、E7、D3、D7、E1、EA、EBを取得します。D3が非搭載の場合は仕様に従って係数1、EBが非搭載の場合は逆方向非対応として扱います。

## 対応環境とハードウェア

- Python 3.12以降
- Windows 11
- Raspberry Pi OS 64-bit（Raspberry Pi 4／5）
- Bルート対応USBドングル RS-WSUHA-P
- Bルート対応低圧スマート電力量メーター

通常の単体テストにRS-WSUHA-Pやスマートメーターは不要です。

## セットアップ

### Ubuntu（WSL2）

Windows 11上のUbuntuターミナルで次を実行します。PythonアプリケーションはWindows側ではなくUbuntu側で動かします。

```bash
cd broute-meter
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp config/settings.example.yaml config/settings.yaml
cp config/credentials.example.yaml config/credentials.yaml
```

### Raspberry Pi OS

```bash
cd broute-meter
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp config/settings.example.yaml config/settings.yaml
cp config/credentials.example.yaml config/credentials.yaml
```

`config/credentials.yaml`、`config/settings.yaml`、`.env`はGit管理対象外です。リポジトリへ登録するのはダミー値を含む`*.example.yaml`だけです。

## 実行経路の追加順序

Dockerは任意の実行手段であり、アプリケーションの必須条件ではありません。開発・実機確認は次の順序で進めます。

1. WindowsホストからRS-WSUHA-PをUbuntu（WSL2）へUSBパススルーし、Ubuntu上でPythonを直接実行する
2. WSL2での直接実行が安定した後、DockerコンテナへのUSB接続を追加する
3. 最後にRaspberry Pi OS上の直接実行と実機接続を確認する

現段階のDocker構成はモック専用です。USBデバイス、特権モード、Bルート認証情報をコンテナへ渡しません。Docker環境の判定やデバイス割当て処理もPythonコードへ入れていません。

## 設定

一般設定は`config/settings.yaml`、Bルート認証情報は`config/credentials.yaml`へ分離します。設定ファイルを省略した項目にはアプリケーション内の既定値が使われます。

```yaml
# config/settings.yaml
measurement:
  instantaneous_interval_seconds: 10
  cumulative_check_interval_seconds: 60

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
  data_directory: "./data"

logging:
  level: "INFO"
  directory: "./logs"
```

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
- `B_ROUTE_DATA_DIR`
- `B_ROUTE_LOG_LEVEL`

瞬時電力間隔は10秒以上でなければなりません。空の環境変数は下位設定へフォールバックせず、設定ミスとして扱います。BルートIDまたはパスワードが不足している場合、通信を開始するコマンドは設定エラーで終了します。

認証情報をコマンドライン引数へ直接書くことは避けてください。シェル履歴やプロセス一覧へ残る可能性があります。`check-config`はパスワードを表示せず、BルートIDも一部だけを表示します。

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

明示指定がない場合、USB機器情報の製品名に`RS-WSUHA-P`を含むポートを候補にします。候補が1件なら使用し、0件または複数件なら勝手に選ばず終了します。公式VID/PIDは公開資料で確認できていないため、未確認値を推測してコードへ入れていません。

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

Docker関連ファイルはこのPythonサブプロジェクト内に閉じています。リポジトリルートのOMK用Composeとは別物なので、必ず`broute-meter`へ移動して実行してください。

```bash
cd broute-meter
docker compose up --build \
  --abort-on-container-exit \
  --exit-code-from broute-meter-mock
```

既定サービスはランタイムイメージで`setup-adapter --mock`を実行して終了します。USBドングルや認証情報は不要です。

Docker内でモック統合テストを実行する場合は、`test`プロファイルを使用します。

```bash
docker compose --profile test run --build --rm tests
```

ランタイムイメージとテストイメージは同じ[Dockerfile](Dockerfile)の別ステージです。通常の直接実行・直接テストは、Dockerをインストールしていない環境でも従来どおり利用できます。

## ログ

標準エラーと`logging.directory`配下の`broute-meter.log`へ出力します。ファイルは日次でローテーションします。ログレベルは`DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`から選べます。

Bルートパスワード、BルートID全文、認証情報ファイル内容は出力しません。後続Phaseのデバッグ電文でも、認証コマンドは必ずマスクします。

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

RS-WSUHA-Pのフラッシュ設定を起動ごとに読み出し、期待値と異なる場合だけ書き込み、再読出しで確認します。無条件の書込みやローカルフラグだけによる判定は行いません。通信要求の再試行、PANA再接続、シリアル再オープンはPhase 7で追加します。

実際のBルートID・パスワードを、ソース、テスト、ログ、README、Issue、コミットへ含めないでください。
