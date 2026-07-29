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
後続タスクの一覧は[ロードマップ](roadmap.md)も参照してください。
