# Raspberry Pi初期セットアップ

## 0. この文書の範囲

この文書は、Raspberry Piを購入してから、OSを書き込み、SSHで接続し、OMKを実行する
ためのDockerホストを準備するまでの手順です。初めて作業する人は、上から順に進めて
ください。

この時点ではOMKの計測サービスは起動しません。Bルート資格情報、センサ設定、実機用
Compose構成、表示、アクセスポイント、LTEなどは後続作業です。これらが未整備の状態で
この文書だけを実行しても、計測開始や外部送信は行われません。

| 段階 | この文書で行うこと | 後続で行うこと |
|---|---|---|
| Raspberry Pi Imager | OS、ユーザー、SSH、ネットワーク、地域設定 | — |
| 初期セットアップ | OS更新、Docker、基本ツール、`data/`作成 | — |
| OMK運用開始 | — | 周辺機器、秘密情報、Composeサービス、表示、ネットワーク |

## 1. 目的と前提条件

この手順は、Raspberry Pi OSを書き込んだ直後のRaspberry Piを、OMKのDockerホスト
として再現可能な状態にします。初期対象はRaspberry Pi 4、64-bit Raspberry Pi OS
（GUIあり）です。OMKの標準ユーザーは`omkdev`、標準配置先は
`/home/omkdev/projects/omk`です。スクリプトは配置場所を動的に判定するため、
別の通常ユーザーや配置先でも実行できますが、標準との差異はログに残ります。

Windows 11のWSL2とVS Codeを開発環境として想定します。Raspberry Piがインターネット
へ接続でき、実行ユーザーが`sudo`を利用できることも必要です。

### 1.1 購入・作業前のチェックリスト

- Raspberry Pi 4またはRaspberry Pi 5本体（新規手順の初期検証対象はPi 4）
- 本体に対応した安定した電源
- Raspberry Pi OSを書き込むmicroSDカードと、Windows PCで書き込むためのカードリーダー
- 有線LAN、またはSSID・パスワード・国設定が分かるWi-Fi
- Raspberry Pi Imagerを利用できるWindows PCとインターネット接続
- SSH接続先を確認するための方法（ホスト名、ルーターのDHCP一覧、または一時的な画面接続）

SSHを有効にすれば、初期設定後にRaspberry Piへキーボードやディスプレイをつなぐ必要は
ありません。周辺機器、公式7インチディスプレイ、LTEドングルはこの初期セットアップの
必須品ではありません。

## 2. Raspberry Pi Imagerで事前設定する項目

Raspberry Pi Imagerで64-bit Raspberry Pi OS（GUIあり）を選び、書き込み前のOS
カスタマイズで次を設定します。

- ユーザー名`omkdev`と十分に強いパスワード
- 設置環境で一意なホスト名
- SSHの有効化（可能なら公開鍵認証）
- Wi-Fiを使う場合のSSID、パスワード、国
- タイムゾーンとキーボードレイアウト

有線LANを使う場合、Wi-Fi設定は不要です。これらの値をセットアップスクリプトが
変更することはありません。

## 3. WindowsからSSH接続する

Windows TerminalまたはPowerShellから、Imagerで設定したホスト名かIPアドレスへ
接続します。

```powershell
ssh omkdev@<ホスト名またはIPアドレス>
```

初回は表示されたホスト鍵のフィンガープリントを、対象Raspberry Piのものだと確認
してから承認します。接続できない場合は、同じネットワークにいること、Raspberry Pi
の起動、IPアドレス、SSH設定を確認してください。

## 4. OMKリポジトリを配置する

標準のOMKリポジトリは次のURLです。

```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/shattori-criepi/omk.git omk
cd omk
```

すでに配置済みなら、既存の作業やデータを確認してから通常のGit手順で更新します。
秘密情報を含むファイルや実測データをコミットしないでください。

## 5. セットアップスクリプトを実行する

リポジトリルートで次を実行します。スクリプト全体を`sudo`で起動する必要はなく、
必要な処理だけが`sudo`を使用します。

```bash
chmod +x scripts/setup-raspberry-pi.sh
./scripts/setup-raspberry-pi.sh
```

OSの更新とパッケージ取得を行うため、完了まで時間がかかる場合があります。途中で
失敗した場合はエラー終了し、同じコマンドで再実行できます。

## 6. スクリプトが実行する処理

- Linux、64-bit ARM、Raspberry Piモデル、実行ユーザー、リポジトリ位置の確認
- `apt update`相当と`apt full-upgrade -y`
- Git、curl、CA証明書、jq、エディタ、診断用基本パッケージの導入
- Docker公式DebianリポジトリからDocker Engine、Buildx、Compose pluginの導入
- Dockerサービスの有効化と起動、バージョン確認
- 実行ユーザーの`docker`グループへの追加
- OMKルート直下の`data/`と標準サブディレクトリの作成
- `logs/setup/setup-YYYYmmdd-HHMMSS.log`への実行結果の記録
- OSが要求する再起動と、Dockerグループ反映に必要な再ログインの案内

Docker EngineとCompose pluginがすでに利用可能なら、Dockerの再導入を省略します。
既存のデータディレクトリ、ファイル、所有者は変更しません。
Docker公式パッケージと競合する別方式のパッケージを検出した場合は、既存環境を
暗黙に置き換えずエラー終了します。

## 7. スクリプトが実行しない処理

OS書き込み、ユーザー・パスワード作成、SSH、ネットワーク、ホスト名、タイムゾーン、
キーボード設定はRaspberry Pi Imagerの責務です。また、次も実行しません。

- BルートID・パスワードやセンサ固有秘密情報の設定
- OMKアプリケーションやDocker Composeサービスの起動
- Chromiumキオスク、公式7インチディスプレイ、アクセスポイント、VNCの設定
- 自動アップデート、自動再起動

これらの後続作業は[ロードマップ](roadmap.md)で管理します。SORACOM Onyx を使う場合は、
基本セットアップの完了後に [SORACOM Onyx セットアップ](soracom-onyx-setup.md)を実行します。

## 8. 再ログインまたは再起動

初めて`docker`グループへ追加された場合、SSHからログアウトして再接続するまで
グループ権限は現在のセッションへ反映されません。再接続の代わりに再起動しても
構いません。`docker`グループのメンバーはホスト上でroot相当の操作が可能になるため、
信頼できる運用ユーザーだけを所属させてください。

OS更新後に`/var/run/reboot-required`が作成されていれば、スクリプト末尾とログに
再起動が必要だと表示します。スクリプトが自動で再起動することはありません。

## 9. Dockerの動作を確認する

再ログイン後、`sudo`なしで次を実行します。

```bash
docker --version
docker compose version
docker run --rm hello-world
```

最初の2コマンドはCLIとCompose plugin、最後のコマンドはデーモン接続、イメージ取得、
コンテナ実行を確認します。OMKのComposeサービスはこの確認だけでは起動しません。

## 10. トラブル発生時に確認する

まず最新のセットアップログを確認します。ログはGit管理対象外です。

```bash
ls -lt logs/setup/
less logs/setup/setup-YYYYmmdd-HHMMSS.log
```

OS、CPUアーキテクチャ、Dockerサービス、ユーザー権限、空き容量を分けて確認します。

```bash
cat /etc/os-release
uname -m
sudo systemctl status docker --no-pager
id
df -h
sudo journalctl -u docker --since today --no-pager
```

`permission denied`でDockerへ接続できない場合は、`id`の出力に`docker`があるか確認し、
SSHへ再接続します。パッケージ取得に失敗する場合は、日時、DNS、インターネット接続、
Dockerのaptソースを確認してから再実行します。既存の別方式のDockerパッケージと
競合した場合は、データをバックアップしたうえで導入方式を整理し、パッケージを
無理に削除しないでください。

## 11. データと秘密情報

OMKの計測データ保存先は、リポジトリルート直下の`data/`です。標準配置なら
`/home/omkdev/projects/omk/data`であり、Docker Composeからは原則として
`./data/...`をマウントします。`/opt/omk/data`や`/var/lib/omk`は使用しません。

実測データ、データベース、CSV、ログはGit管理対象外です。BルートID・パスワード、
Wi-Fiパスワード、SSH秘密鍵、APIキーなどの秘密情報を、リポジトリやセットアップ
ログへ保存しないでください。

## 12. 運用開始前の引き継ぎチェック

初期セットアップを完了しただけでは、計測を始められません。次の項目は担当者、設定値、
確認日を記録してから別手順として実施してください。

- 対象Raspberry Piのホスト名、IPアドレス、設置場所、OSバージョン
- SSH公開鍵、ログイン可能な運用ユーザー、バックアップ方針
- Bルート、MQTT、LTE、外部送信先などの秘密情報の保管場所
- 接続するUSB、Bluetooth、ESP32、ディスプレイの型番と設定手順
- 実機向けCompose構成、起動・停止・障害時の確認方法
- `data/`の容量監視、バックアップ、保持期間、削除手順

秘密値そのものを、この文書、Git、セットアップログ、チケットへ記載しないでください。

## 13. Dockerデータ収集・表示サービスを起動する

Wi-Fiアクセスポイントを先に設定し、`wlan0` に `192.168.50.1/24` が割り当てられた後で、
次のスクリプトを実行します。このアドレスはMosquittoの公開先としてComposeに固定されて
いるため、APが未設定のまま起動すると安全に失敗します。APの設定は
`scripts/setup-wifi-access-point.sh` を使用してください。

このスクリプトが本番起動するのは、次の3サービスだけです。

- `mosquitto` (`omk-mosquitto`)
- `sensor-collector` (`omk-sensor-collector`)
- `dashboard` (`omk-dashboard`)

`gateway`、`broute-meter-mock`、`broute-meter-tests` は起動しません。Bルート本体は
ホストのsystemdサービスとして動作し、Docker化しません。

Docker EngineとCompose pluginが利用でき、実行ユーザーがDocker groupを反映済みである
こと、`curl`、Compose設定、各Dockerfileが存在することが前提です。最初に予定と
非秘密設定を確認できます。

```bash
./scripts/setup-data-collection.sh --dry-run
./scripts/setup-data-collection.sh --print-config
```

通常の起動、対象イメージの取得、対象アプリケーションのビルド、明示的な再起動はそれぞれ
次のとおりです。通常実行は既に正常稼働中のサービスを不要に再作成しません。

```bash
./scripts/setup-data-collection.sh
./scripts/setup-data-collection.sh --pull
./scripts/setup-data-collection.sh --build
./scripts/setup-data-collection.sh --restart
```

`--pull` は配布イメージであるMosquittoだけを取得します。`sensor-collector` と
`dashboard` はレジストリからpullしないローカルbuildサービスです。これらのベースイメージも
更新して再buildする場合は、`--pull --build` を併用してください。

実行ログは `logs/setup/setup-data-collection-YYYYMMDD-HHMMSS.log` に保存されます。
Dashboardは [http://localhost:8000/display](http://localhost:8000/display)、health確認は
[http://localhost:8000/health](http://localhost:8000/health) です。状態とログは次で確認します。

```bash
docker compose ps mosquitto sensor-collector dashboard
docker compose logs --tail 100 mosquitto sensor-collector dashboard
```

停止・再起動も必ず対象サービスを明示します。`docker compose down` や特に
`docker compose down -v` は、他サービス、ネットワーク、またはデータへ影響し得るため
使用しないでください。

```bash
docker compose stop mosquitto sensor-collector dashboard
docker compose restart mosquitto sensor-collector dashboard
```

`data/sensors`（JSONL）、`data/latest`（表示用最新状態JSON）、`data/processed`（処理済みデータ）、
`services/mosquitto/data` は運用データです。削除せず、バックアップと保持方針に従って
管理してください。スクリプトは既存ディレクトリを再帰的にchownしません。

`data/latest`はcollectorが書き込み、dashboardは読み取り専用で参照します。初回の計測後に`ls -l data/latest/`で`broute_power.json`、`sen66.json`、`ichijo_power_flow.json`の生成状況と読取り権限を確認してください。未生成でもdashboardは起動し、欠損値として表示します。

### トラブルシューティング

- `192.168.50.1` がない: `nmcli connection show omk-ap` と
  `ip -4 address show wlan0` を確認し、AP設定を完了してから再実行します。Composeのbindを
  `0.0.0.0` に書き換えないでください。
- Docker権限エラー: `systemctl status docker` を確認し、`docker` group追加後はSSHから
  ログアウト・再接続します。恒常的に `sudo docker` で運用しないでください。
- Dashboard health失敗: `docker compose logs --tail 100 dashboard` とポート8000の利用状況を
  確認します。`ls -l data/latest/`でlatest JSONと権限を、`docker compose exec dashboard sh -c 'test -r /app/data/latest'`で読取りマウントを、`curl http://localhost:8000/api/display`でAPI応答を確認します。latestまたはprocessedデータがまだなくてもDashboard自体は起動できます。
- sensor-collectorのMQTT接続失敗: Mosquittoの状態、`192.168.50.1:1883`、および
  `MQTT_HOST=mosquitto`、`MQTT_PORT=1883`、`MQTT_TOPIC=omk/#` を確認します。センサが
  未送信でもセットアップは成功します。

## 14. data-transformerの定期実行

sensor-collectorが保存するJSONLをParquetへ変換するには、リポジトリ更新後に以下を実行します。これはDockerコンテナではなくRaspberry Piホスト上のsystemd timerを設定します。

```bash
cd ~/projects/omk
./scripts/setup-data-transformer.sh
```

timerは起動後約2分、その後1時間ごとにJST当日と前日の既存JSONLを変換します。確認、ログ確認、停止は以下を使用します。

```bash
sudo systemctl status omk-data-transformer.timer --no-pager
systemctl list-timers omk-data-transformer.timer
sudo journalctl -u omk-data-transformer.service --since today --no-pager
sudo systemctl disable --now omk-data-transformer.timer
```

## Dashboard Chromiumキオスク

dashboardコンテナは画面データを提供し、Chromiumは別のuser systemdサービスとしてWayland GUIセッション内で表示します。SSHからChromiumを直接起動しないため、NapterやSSHの切断では画面は停止しません。

先に`dashboard`を含むComposeサービスを起動し、Raspberry PiのGUIへ対象ユーザーで自動ログインした状態で次を実行します。

```bash
cd ~/projects/omk
./scripts/setup-dashboard-kiosk.sh
```

スクリプトは`chromium`、`curl`、`wtype`、user managerのbus、`/run/user/<UID>`、Waylandソケットを確認し、`wtype`がなければ`apt`で導入します。`~/.config/systemd/user/omk-dashboard-kiosk.service`を更新するとともに、`~/.config/labwc/rc.xml`へカーソル非表示用の`A-W-h`キーバインドを追加します。既存のタッチ設定などは保持し、変更時だけ日時付きバックアップを作成します。`openbox_config`のルート要素はlabwc用の`labwc_config`へ変換します。`WAYLAND_DISPLAY`はuser manager環境から取得し、未設定時だけ`wayland-0`を使います。異なる場合は`DASHBOARD_KIOSK_WAYLAND_DISPLAY=...`を付けて実行してください。既存の手動unitは内容が異なる場合にバックアップして置き換え、旧`graphical-session.target.wants`のsymlinkは安全に削除します。

GUI自動ログイン時にuser managerの`default.target`が起動し、キオスクunitも有効化されます。unit自身がWaylandソケットとdashboard healthを待つため、表示準備前にChromiumを起動しません。lingerは不要です。GUIセッションがなくuser managerが停止している状態ではChromiumは起動しません。確認、再起動、ログ、停止は以下です。

セットアップ実行時にWaylandソケットがまだない場合は安全にエラー終了します。GUIログイン後に再実行してください。いったん有効化されたunitは、以後の自動起動時にはソケットとhealth応答を待機します。

```bash
systemctl --user status omk-dashboard-kiosk.service --no-pager
systemctl --user restart omk-dashboard-kiosk.service
journalctl --user -u omk-dashboard-kiosk.service --no-pager
systemctl --user disable --now omk-dashboard-kiosk.service
```

Chromiumを終了またはkillした場合は、systemdが約5秒後に再起動します。dashboard未起動時はhealth endpointの応答まで待機します。Chromium起動後はunitの`ExecStartPost`が`wtype -M alt -M logo -P h`を実行し、labwcの`HideCursor`と`WarpCursor`によりカーソルを非表示にします。

カーソル非表示が効かない場合は、次を確認してください。

```bash
command -v wtype
grep -E 'labwc_config|HideCursor|WarpCursor' ~/.config/labwc/rc.xml
systemctl --user cat omk-dashboard-kiosk.service
```

SSH経由でlabwc設定の即時再読込が必要な場合は、GUIセッションのlabwc PIDを指定して`LABWC_PID=<pid> ./scripts/setup-dashboard-kiosk.sh`を実行します。再読込できなくても、次のGUIセッションまたは再起動で設定が反映されます。
後続タスクの一覧は[ロードマップ](roadmap.md)も参照してください。

## 14. Wi-Fiアクセスポイント（NetworkManager）

`scripts/setup-wifi-access-point.sh`は、Raspberry Pi OS/DebianのNetworkManagerへOMK用の
Wi-Fiアクセスポイント接続プロファイルを安全に作成または更新するためのスクリプトです。
Docker、Bルート、表示、kioskなどは設定しません。既定の接続名は`omk-ap`、インター
フェースは`wlan0`、IPv4は`192.168.50.1/24`です。プロファイルは`ipv4.method shared`と
WPA2-PSKを使用します。

まず、変更を行わないdry-runで解決済みの値を確認します。

```bash
./scripts/setup-wifi-access-point.sh --dry-run
```

通常の実行は次のとおりです。SSIDを指定しなかった場合は、既存の対象プロファイルの
SSIDを優先し、なければ`OMK-XXXXXX`を生成します。`XXXXXX`は`/etc/machine-id`を
SHA-256でハッシュした先頭6桁の大文字16進数であり、端末ごとに概ね安定し、生の
machine-idやMACアドレスを公開しません。

```bash
OMK_AP_SSID='任意のSSID' ./scripts/setup-wifi-access-point.sh
```

PSKが必要な場合、環境変数に設定していなければスクリプトが非表示で入力を求めます。
画面・ログ・`nmcli`のコマンドライン引数には出力されません。設定時は`nmcli connection
edit`へ標準入力で渡すため、`sudo`経由でもPSKはプロセス引数に含まれません。環境変数で
渡すこともできますが、権限のあるプロセスから環境が読める可能性があるため、可能なら
対話入力を使い、PSKをGitへ保存しないでください。

```bash
./scripts/setup-wifi-access-point.sh
# WPA2-PSK (input is hidden): と表示されたら画面を見ながら入力する
```

既存の`omk-ap`プロファイルがある場合、スクリプトはID、インターフェース、autoconnect、
APモード、SSID、鍵管理、IPv4方式・アドレスを確認します。期待値と一致すれば変更せず
終了します。相違があれば、PSKを除く変更予定を表示して明示確認を求め、削除・作り直し
ではなく`nmcli connection modify`で更新します。更新前の秘密情報を含まない設定スナップ
ショットと実行ログは`logs/setup/`に保存され、Git管理対象外です。

通常実行はプロファイルを作成・更新するだけで、APを有効化しません。AP有効化はWi-Fi
接続を切り替えるため、SSHが`wlan0`経由の場合は接続が切れる可能性があります。別WAN
（たとえばモバイル回線）が有効であることを確認し、できればローカルコンソールから実施
してください。明示的に有効化するには次を実行し、表示される確認に応答します。

```bash
./scripts/setup-wifi-access-point.sh --activate
```

状態確認、停止、autoconnect無効化、プロファイル削除は次のコマンドです。削除は復元が
必要になるため、対象名を確認してから手動で実行してください。

```bash
nmcli connection show --active
nmcli device status
sudo nmcli connection down omk-ap
sudo nmcli connection modify omk-ap connection.autoconnect no
sudo nmcli connection delete omk-ap
```

元へ戻すには、APを停止しautoconnectを無効化します。削除前に保存した`logs/setup/`の
スナップショットを参照して、必要なら`nmcli connection modify`で以前の非秘密設定へ
戻してください。PSKを含む秘密情報は、この文書、ログ、Git、チケットへ記載しません。
