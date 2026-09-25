# Gatewayセットアップ

このページは、新品のmicroSDカードからOMK Gatewayを構築する手順です。OMKで動作確認している推奨構成は、**Raspberry Pi 4、Raspberry Pi Touch Display 2（7インチ）、SORACOM Onyx**です。Windows PCからSSHで操作し、Dashboardの表示、OMK AP、Onyxによる外部通信を設定します。

## 用意するもの

- Raspberry Pi 4と安定した電源
- Raspberry Pi Touch Display 2（7インチ）
- 64GB以上を目安にしたmicroSDカードとカードリーダー
- SORACOM Onyx
- 利用開始済みのSORACOM Air SIM（nano SIM）。通常用途にはplan-Dを推奨します。
- Windows PC（Raspberry Pi ImagerによるOS書き込みとSSH接続に使用）
- インターネットに接続できる既存Wi-Fi
- USBキーボード（Gatewayを直接操作する場合）

部品例は[OMKの参考パーツ構成](parts-list.md)を参照してください。センサやOMK NodeはGatewayの構築後に追加します。

## 1. Raspberry Pi ImagerでOSを書き込む

Windows PCにmicroSDカードを接続し、Raspberry Pi Imagerで次を選びます。

- 機器：Raspberry Pi 4
- OS：**Raspberry Pi OS（Debian 13 / Trixie、64ビット、デスクトップあり）**
- 書き込み先：用意したmicroSDカード

書き込み前のOSカスタマイズで、次を設定してください。

| 項目 | 設定内容 |
| --- | --- |
| ホスト名 | 同じネットワーク内で重複しない名前（例：`omk-gateway`） |
| 地域（Localisation） | Tokyoを選択し、タイムゾーンが`Asia/Tokyo`になっていることを確認する |
| ユーザー名 | `omkdev` |
| パスワード | 自分で決めたパスワード。SSH接続と`sudo`で使うため、安全な場所に記録する |
| Wi-Fi | 初期設定に使う既存Wi-FiのSSIDとパスワード |
| SSH | `Enable SSH`を有効にし、`Use password authentication`を選択 |
| Raspberry Pi Connect | `Enable Raspberry Pi Connect`はOFF |

Imagerの画面操作は[Raspberry Pi公式ガイド](https://www.raspberrypi.com/documentation/computers/getting-started.html#install-using-imager)も参照してください。

書き込みと検証が完了したら、microSDカードをRaspberry Piへ挿入します。OnyxはUSB接続する前に、本体の表示に合わせてSIMの金属端子の向きを確かめ、SIMを挿入します。電源を入れる前にTouch Display 2とOnyxを接続し、電源を入れて起動を待ちます。

## 2. GatewayへSSH接続する

この手順ではWindows PowerShellからSSH接続します。PuTTYなど、他のSSHクライアントを使用しても構いません。

PowerShellを使う場合は、まず次を実行します。

```powershell
ssh -V
```

OpenSSHのバージョンが表示されれば、下のSSH接続手順へ進みます。`ssh`コマンドが見つからない場合は、Windowsの「オプション機能」から「OpenSSH Client」を追加し、もう一度`ssh -V`を実行してください。

Windows PCをRaspberry Piと同じ既存Wi-Fiへ接続し、Windows PowerShellで次を実行します。`<ホスト名>`はImagerで設定した名前に置き換えてください。

```powershell
ssh omkdev@<ホスト名>.local
```

初回は接続先の確認が表示されます。表示された接続先を、Imagerで設定したホスト名に`.local`を付けたものと照合します。IPアドレスを指定した場合は、[Gateway上で調べた値](troubleshooting.md#windowsからホスト名で接続できない)と照合します。一致していたら`yes`を入力し、続いてImagerで設定したパスワードを入力します。パスワード入力中は文字や`*`が表示されません。入力後にEnterを押してください。

ホスト名で接続できない場合は、[SSH接続できない](troubleshooting.md#ssh接続できない)を参照してください。

## 3. OMKを取得する

SSH接続先のGatewayで、次を実行します。

```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/shattori-criepi/omk.git omk
cd omk
```

## 4. Gatewayのセットアップを開始する

```bash
./scripts/setup-omk-gateway.sh --with-base --with-soracom
```

`sudo`のパスワードを求められたら、Imagerで設定した`omkdev`のパスワードを入力します。

<a id="5-再起動または再ログインを求められた場合"></a>

## 5. 基本設定後にセットアップを再開する

OSなどの基本設定後に再起動または再ログインが必要な場合、セットアップはいったん停止して操作を案内します。画面に表示された方の操作を行い、接続し直してセットアップを再開します。案内なしで処理が続く場合は、そのまま手順6へ進んでください。

### 再起動のメッセージが表示された場合

```text
A reboot is required. Reboot, reconnect, then rerun this command without --with-base to resume.
```

Gatewayで次を実行します。

```bash
sudo reboot
```

起動を待ち、手順2と同じコマンドでSSH接続し直します。

### 再ログインのメッセージが表示された場合

```text
Docker group membership needs a new login session. Re-login, then rerun this command without --with-base to resume.
```

`exit`でSSHを切断し、手順2と同じコマンドで接続し直します。

### 再接続後にセットアップを再開する

どちらの場合も、再接続後は次を実行します。**再開時は`--with-base`を外してください。**

```bash
cd ~/projects/omk
./scripts/setup-omk-gateway.sh --with-soracom
```

## 6. OMK APへの切り替え

OMK APは、PCやOMK NodeをGatewayへ接続するためのWi-Fiアクセスポイントです。有効にすると、Raspberry Piの内蔵Wi-FiはOMK AP専用になり、これまでのWi-Fi経由のSSH接続やインターネット通信には使えなくなります。これ以降の外部通信にはOnyxを使います。

セットアップ中に表示される確認には、次の順に答えてください。入力後はEnterを押します。

| 確認メッセージ | 入力・操作 |
| --- | --- |
| `Apply these NetworkManager profile changes? [y/N]` | `y`を入力する |
| `Show the OMK AP password now? [y/N]` | 既定の`N`のままEnterを押す |
| `Activate omk-ap now? This can disconnect SSH [y/N]` | `y`を入力する |

Touch Display 2でDashboardを使う標準構成では、OMK APのSSID・パスワードを控える必要はありません。OMK NodeのWi-Fi設定もNodeセットアップ時に自動で行われます。PCやタブレット等でDashboardを見る場合だけ、後から[「OMKアクセスポイント参照」](dashboard.md#omk-apの接続情報を見る)で確認します。

OMK APに接続できる端末からはDashboardを閲覧できるため、**SSIDとパスワードは第三者へ不用意に共有しないでください。**

APを有効にすると、既存Wi-Fi経由のSSHは切断されますが正常です。Gatewayのセットアップは切断後も完了まで進みます。

### AP切替後の操作方法

以降は、NapterでWindows PCからSSH接続するか、USBキーボードを使ってGatewayを直接操作します。どちらの場合も、手順7・8のコマンドをGateway上で実行します。

#### Napterを使う場合

SORACOM Napterは、SORACOM回線を通してGatewayへ遠隔接続するサービスです。

1. Windows PCは、インターネットに接続できる既存Wi-Fiへ接続したままにします。
2. SORACOMユーザーコンソールで、Onyxに入れたSIMのセッション状態が「オンライン」になったら、Napterのオンデマンドリモートアクセスを開始します。
3. [SORACOM公式のSSH接続手順](https://users.soracom.io/ja-jp/docs/napter/login-with-ssh/)の「PCのターミナルを使ってSSH接続する」に従い、Windows PowerShellから接続します。接続先・ポートはNapterに表示された値、ユーザー名は`omkdev`、パスワードはImagerで設定したものを使います。

接続できたら、手順7のコマンドを実行します。つながらない場合は[Napterのトラブルシューティング](troubleshooting.md#napterでssh接続できない)を参照してください。

#### Gatewayを直接操作する場合

GatewayにUSBキーボードを接続し、Touch Display 2の画面で`Ctrl+Alt+T`を押してターミナルを開きます。以降は、手順7のGateway上のコマンドをそのまま実行してください。

## 7. BLE Sensor Managerを設定する

BLEセンサの登録やOMK Nodeの機器管理・BLE中継に使う機能を導入します。Gateway上で次を実行してください。パッケージの取得にはOnyx経由のインターネット接続を使います。

```bash
cd ~/projects/omk
./scripts/setup-ble-sensor-manager.sh
```

セットアップが完了したら次へ進みます。BLEセンサとOMK Nodeをどちらも使わない場合だけ、この手順を省略できます。

## 8. Bルートを使用する場合は設定する

低圧スマートメーターのBルートを使う場合は、利用申請後に電力会社から提供された、Bルートに接続するためのIDとパスワードを用意します。この時点でGatewayへ追加接続する機器はRS-WSUHA-Pだけです。OMK NodeはGateway構築後に接続します。Bルートを使わない場合は手順9へ進んでください。

Gateway上で次を実行します。

```bash
cd ~/projects/omk
./scripts/setup-broute-meter.sh
```

`Use this FT230X serial adapter as RS-WSUHA-P? ... [y/N]`と表示されたら、`y`を入力します。

IDとパスワードは、Gatewayの構築後に[DashboardのBルート設定](dashboard.md#bルートを設定する)で登録します。

## 9. セットアップ完了を確認する

次の2点を確認できれば、Gatewayの構築は完了です。

- Touch Display 2に日時や計測値を表示するDashboardの画面が出ており、右上に管理メニューを開く歯車アイコンがある
- PCやスマートフォンのWi-Fi一覧に、Gatewayの`OMK-XXXXXX`形式のSSIDが表示される

この時点ではまだセンサを追加していないため、計測データが表示されていなくても正常です。

続いて、使いたいセンサや機器を追加します。

1. [対応センサ・機器と取得データ](supported-devices.md)で、計測したい項目に対応する機器を選びます。
2. 必要な機器を用意します。OMK Nodeを使う場合は、[OMK Nodeの役割と設置](node-and-sensors.md)で使い方と設置場所を確認します。
3. Nodeが必要なら、[AtomS3 LiteへのOMK Node導入](esp32-node-setup.md)で準備・登録します。
4. [Dashboardの使い方](dashboard.md)へ進み、BLEセンサの登録や表示設定を行います。

構築後の更新・機能追加は[Gatewayの更新・保守](gateway-maintenance.md)、問題が起きた場合は[トラブルシューティング](troubleshooting.md)を参照してください。

## 標準構成以外で使う場合

別のディスプレイ、ディスプレイなし、Onyxなしの構成も利用できますが、OMKでは動作確認していません。

- Onyxを使わない場合は、手順4・5のコマンドから`--with-soracom`を外します。AP切り替え後のセットアップにもインターネット接続が必要なため、有線LANなどの通信経路を用意してください。
- ディスプレイを使わない場合は、手順4・5のコマンドに`--no-kiosk`を追加します。DashboardはOMK APに接続したPCなどで利用します。
- plan-DUのSIMを使う場合は、手順4・5のセットアップコマンドの先頭に`SORACOM_APN=du.soracom.io`を付けて実行します。

PCやタブレット等からDashboardを見る場合は、[Dashboardの任意の接続方法](dashboard.md#pcやタブレット等からdashboardを使う場合)を参照してください。

### SSHで設定を続ける場合

Onyxを使わない場合は、PCをOMK APへ接続し、`ssh omkdev@192.168.50.1`でGatewayへ接続できます。後続のセットアップには、Gatewayに有線LANなど別のインターネット接続経路が必要です。
