# 2-3. Gatewayセットアップ

[「2-2. Gatewayの組み立て」](gateway-assembly.md)を終えたGatewayに、OSとOMKの標準機能を設定します。Windows PCから始め、OMK AP切り替え後はGatewayのTouch Display 2とUSBキーボードで操作します。**最後の完了確認まで進めてから、センサを追加してください。**

## この手順の前に

- Touch Display 2、ケース、Raspberry Pi 4の組み立てを済ませ、USBキーボードを接続します。
- 利用開始済みのSORACOM Air SIM（nano SIM、plan-D）をOnyxへ挿し、OnyxをGatewayへUSB接続しておきます。ほかのUSBモデムは接続しません。
- 64GB以上を目安にしたmicroSD、カードリーダー、Windows PC、インターネットに接続できる既存Wi-Fiを用意します。テザリングを使う場合は、作業中に自動停止しない設定にしてください。
- Bルートを使い、RS-WSUHA-Pを用意している場合は、組み立て時に接続したまま進めます。未使用・未入手なら未接続でも完了できます。
- AtomS3 LiteとSEN66はまだ接続しません。Gatewayの電源はOS書き込み後に入れます。

部品は[「2-1. OMKのパーツ構成」](parts-list.md)、全体の順序は[「1-1. OMK導入ガイド」](getting-started.md)で確認できます。コマンドは1つずつ実行し、エラーが出たら次へ進まず[「5-3. トラブルシューティング」](troubleshooting.md#パッケージやomkのダウンロードが失敗する)を参照してください。

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

書き込みと検証が完了したら、PCからmicroSDを安全に取り外してGatewayのカードスロット（組み立てたケースのmicroSD延長部品）へ挿します。Touch Display 2、SIM入りOnyx、USBキーボードの接続を確かめ、Piの電源を入れて起動を待ちます。

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

初回は接続先の確認が表示されます。表示された接続先を、Imagerで設定したホスト名に`.local`を付けたものと照合します。IPアドレスを指定した場合は、[「5-3. トラブルシューティング」](troubleshooting.md#windowsからホスト名で接続できない)の手順でGateway上のIPアドレスを調べ、その値と照合します。一致していたら`yes`を入力し、続いてImagerで設定したパスワードを入力します。パスワード入力中は文字や`*`が表示されません。入力後にEnterを押してください。

ホスト名で接続できない場合は、[「5-3. トラブルシューティング」](troubleshooting.md#ssh接続できない)を参照してください。

## 3. GitHubからOMKのソースコードを取得する

SSH接続先のGatewayで、次を実行します。`git clone`は、GitHubからOMKのソースコード一式を`~/projects/omk`へダウンロードするコマンドです。

```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/shattori-criepi/omk.git omk
cd omk
```

## 4. Gatewayのセットアップを開始する

以下はGateway上のコマンドです。初回だけ`--with-base`を付け、OS更新とDockerを導入します。`--with-soracom`により、基本設定の後にOnyxの設定も実行されます。Onyxはこのコマンドの前から接続しておいてください。

```bash
./scripts/setup-omk-gateway.sh --with-base --with-soracom
```

`sudo`のパスワードを求められたら、Imagerで設定した`omkdev`のパスワードを入力します。

<a id="5-再起動または再ログインを求められた場合"></a>

## 5. 基本設定後にセットアップを再開する

OSなどの基本設定が`Setup result: SUCCESS`で完了し、再起動または再ログインが必要な場合、セットアップはいったん停止して操作を案内します。画面に表示された方の操作を行い、接続し直してセットアップを再開します。案内なしで処理が続く場合は、そのまま手順6の確認メッセージへ進みます。`Setup result: FAILURE`なら基本設定未完了なので、再起動表示だけで完了と判断せず[「5-3. トラブルシューティング」](troubleshooting.md#パッケージやomkのダウンロードが失敗する)を参照してください。

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

手順4・5のスクリプトは、Onyx設定、Dashboardとデータ保存などのサービス準備・起動を行い、最後にOMK APへ切り替えます。**ここでスクリプトを追加実行する必要はありません。**

OMK APはGateway・Node・管理端末をつなぐローカルネットワークです。PCやNodeのインターネット通信はGateway経由では転送しません。外部通信はGateway自身がOnyxを使うため、AP側端末の通信でSORACOM回線を意図せず消費しません。

有効化するとPiの内蔵Wi-FiはOMK専用となり、既存Wi-Fiから切断されます。**切り替え前に、PCのSORACOMユーザーコンソールで対象SIMのセッション状態が「オンライン」になっていることを確認してください。** つながらなければ[「5-3. トラブルシューティング」](troubleshooting.md#onyxで外部通信できない)を確認してから進みます。 Onyx設定で`Optional external DNS/HTTPS reachability check failed`等の`WARNING`が出た場合も、後続のダウンロードに必要な外部通信を復旧してからAPを有効化します。SIMの「オンライン」だけでは外部通信まで確認できません。

実行中の確認には、表示された順に答えます。設定済みの項目では一部の確認が省略される場合があります。

| 確認メッセージ | 入力・操作 |
| --- | --- |
| `Apply these NetworkManager profile changes? [y/N]` | `y`を入力する |
| `Show the OMK AP password now? [y/N]` | 既定の`N`のままEnterを押す |
| `Activate omk-ap now? This can disconnect SSH [y/N]` | Onyxの接続を確認後、`y`を入力する |

SSID・パスワードは後でTouch Display 2の「OMKアクセスポイント参照」で見られます。操作は[「4-1. Dashboardの使い方」](dashboard.md#omk-apの接続情報を見る)を参照してください。NodeのWi-Fi設定はNodeセットアップ時に自動で行われます。APの接続情報は第三者へ不用意に共有しないでください。

最終AP切り替えは独立した処理として実行されるため、この時点のSSH切断後も進みます。`queued`という表示は切り替えの受付であり、完了の判定は手順9で行います。これより前のダウンロード中などの通信断は、[「5-3. トラブルシューティング」](troubleshooting.md#パッケージやomkのダウンロードが失敗する)で対処してください。

### AP切替後の操作方法

ここからは**GatewayのTouch Display 2とUSBキーボード**を使います。`Ctrl+Alt+T`でターミナルを開き、次の手順7・8のコマンドを本体上で実行します。パスワードを求められたらImagerで決めた`omkdev`のパスワードを入力します。ダウンロードにはOnyxを使います。

PCのWi-Fi一覧でGatewayの`OMK-XXXXXX`形式のSSIDが見えることを確かめ、次へ進んでください。見つからない場合は[「5-3. トラブルシューティング」](troubleshooting.md#omk-apがwi-fi一覧に見えない)を参照します。

## 7. BLE Sensor Managerを設定する

BLEセンサの登録やOMK Nodeの機器管理・BLE中継に使う機能を導入します。Gateway上で次を実行してください。パッケージの取得にはOnyx経由のインターネット接続を使います。

```bash
cd ~/projects/omk
./scripts/setup-ble-sensor-manager.sh
```

標準Gatewayの機能として、この手順を完了してください。個別センサの登録はまだ行いません。`--with-ble`は使用せず、Gateway側のMQTTサービスが起動した後に、この個別スクリプトで導入します。

```bash
systemctl is-active omk-ble-sensor-manager.service
```

`active`なら次へ進みます。それ以外ならセットアップのエラーを確認し、[「5-3. トラブルシューティング」](troubleshooting.md#パッケージやomkのダウンロードが失敗する)を参照してください。

## 8. Bルートサービスを準備する

### この手順の前に

RS-WSUHA-Pを組み立て時に接続した場合は、そのままにします。AtomS3 Liteなど、ほかのUSBシリアル機器は接続しません。Onyxは外部通信のため接続したままにします。この工程ではBルートサービスを導入し、接続済みならアダプタも設定します。BルートID・パスワードはまだ不要です。

Gateway本体のターミナルで実行します。

```bash
cd ~/projects/omk
./scripts/setup-broute-meter.sh
```

- RS-WSUHA-Pを接続している場合：`Use this FT230X serial adapter as RS-WSUHA-P? ... [y/N]`と表示されたら、接続した実物がRS-WSUHA-Pであることを確認して`y`を入力します。シリアルポートが設定され、サービス起動を試みます。設定済みなら、この確認は表示されません。抜き差しを求められた場合はRS-WSUHA-Pだけを抜き、数秒待って同じUSB端子へ接続し直します。
- アダプタ未接続の場合：シリアルポート未設定のまま、サービスを導入・有効化して終了します。アダプタを用意してから設定できます。

アダプタやBルートID・パスワードが未設定である旨の警告は、この段階では正常です。アダプタを接続していても、認証情報が未登録ならサービスが`active`にならない場合があります。サービスが停止していることも起動していることも、Gatewayの完了条件にはしません。`ERROR`で終了した場合は次へ進まず、[「5-3. トラブルシューティング」](troubleshooting.md#パッケージやomkのダウンロードが失敗する)を参照してください。

```bash
systemctl is-enabled omk-broute-meter.service
```

スクリプトがエラーなく完了し、`enabled`ならサービスの準備完了です。接続したRS-WSUHA-Pの確認に`y`と答えてアダプタ設定も済ませた場合は、3章でのスクリプト再実行は不要です。認証情報の入力と計測確認は、Gatewayのセットアップ後に[「3-1. センサ・計測機器を追加する」](sensor-setup.md#bルートでスマートメーターを追加する)で行います。

## 9. セットアップ完了を確認する

ターミナルを閉じ、Touch Display 2で次を確認します。

- Dashboardの表示画面が開き、右上の歯車から管理メニューを操作できる。
- 「機器管理」が開き、BLE Sensor Managerへの接続エラーが出ない（機器は未登録で構いません）。
- 「Bルート設定」が開く。ID・パスワードは「未設定」で正常です。接続状態の表示はアダプタ設定やサービス起動状態によって変わるため、「サービス停止」を固定の条件にしません。認証情報はセンサ追加の手順で入力します。
- 「OMKアクセスポイント参照」のSSIDが、PCやスマートフォンのWi-Fi一覧に見える。
- 手順7・8のセットアップがエラーなく完了し、BLEサービスは`active`、Bルートサービスは`enabled`になっている。

センサを追加していないので、計測値が空でも正常です。画面が出ない場合は[「5-3. トラブルシューティング」](troubleshooting.md#touch-display-2にdashboardが表示されない)を参照してください。

**これでGatewayのセットアップが完了しました。** Dashboard・保存機能・OMK APとGateway側のBLE・Bルート機能が準備できました。

次へ：[「3-1. センサ・計測機器を追加する」](sensor-setup.md)から、SEN66 Nodeを1台製作して計測を始めます。
