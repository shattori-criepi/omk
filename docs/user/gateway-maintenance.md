# Gatewayソフトウェアを更新・保守する

構築済みGatewayの更新、機能追加、再設定、状態確認を説明します。初めて構築する場合は[「2-3. Gatewayセットアップ」](gateway-setup.md)、問題が起きた場合は[「トラブルシューティング」](troubleshooting.md)を参照してください。

## GatewayのOMKソフトウェアを更新する

更新にはGateway自身のインターネット接続が必要です。標準構成ではSORACOM Onyxを使います。Onyxを使わない場合は、有線LANなどの接続を用意してください。OMK APは機器とGatewayをつなぐローカルネットワークで、AP側端末の外部通信は転送しません。

更新中はDashboardや計測が一時的に停止します。セットアップには既存データや認証情報を保持する処理がありますが、サービスの再起動、設定ファイル・権限の更新、旧設定の移行も行います。実行前に[「4-1. Dashboardの使い方」](dashboard.md#データを書き出す)に従って重要な計測データをUSBへ書き出し、独自設定がある場合は控えを残してください。

### 1. Gatewayへ接続する

Gatewayへの接続・操作方法は、本ページの[構築後のGatewayへ接続する](#構築後のgatewayへ接続する)を参照してください。

以下のコマンドはGateway上で実行してください。

```bash
cd ~/projects/omk
git status --short
```

何も表示されなければ、次へ進みます。ファイル名が表示された場合は、手元の変更や追加ファイルがあるため、ここで更新を止めてください。この手順では、変更のない状態からの更新を扱います。

### 2. GitHubからOMKソースコードの最新版を取得する

`git pull`は、GitHub上の更新を取得して、Gateway内のOMKソースコードを更新するコマンドです。稼働中のサービスへの反映は、次のセットアップ再実行で行います。

```bash
git pull --ff-only
```

取得が完了したら次へ進みます。`Already up to date.`と表示された場合は、すでに最新版です。

### 3. セットアップを再実行する

標準構成では次を順に実行します。**通常の更新では`--with-base`を付けず、BLEは個別スクリプトで更新します。** 各コマンドが完了したことを確認してから次を実行してください。

```bash
./scripts/setup-omk-gateway.sh --with-soracom
```

APの確認メッセージには[「2-3. Gatewayセットアップ」](gateway-setup.md#6-omk-apへの切り替え)に沿って答えます。最終AP切り替え処理はSSH切断後も進みますが、前段のパッケージ導入等に同じ保証はありません。切断された場合は[接続し直して](#構築後のgatewayへ接続する)、次を実行します。

```bash
cd ~/projects/omk
./scripts/setup-ble-sensor-manager.sh
```

続いて、Bルートサービスを更新します。利用中のRS-WSUHA-Pは接続したまま、AtomS3 LiteなどほかのUSBシリアル機器は作業中だけ外します。Onyxは外部通信のため接続したままにします。Bルート未使用ならアダプタ未接続のままで実行できます。

```bash
./scripts/setup-broute-meter.sh
```

Bルート計測は再実行中に停止する場合があります。FT230Xの実物確認が出たらRS-WSUHA-Pの接続を確認して答えます。

Onyxなしや表示端末を変更した構成では、[「標準構成以外・高度な構成」](advanced-configuration.md)に従って最初のコマンドを調整します。失敗時は[「トラブルシューティング」](troubleshooting.md#パッケージやomkのダウンロードが失敗する)へ進みます。

### 4. 更新後の動作を確認する

セットアップが完了したら、[状態を確認する](#状態を確認する)の表に沿って、Dashboardと使用中の機器の表示を見ます。

Gatewayを更新しても、設置済みOMK Nodeのファームウェアは自動更新されません。Nodeの更新は[「OMK Nodeを更新・再設定する」](node-maintenance.md#ファームウェアを更新する)を参照してください。

## セットアップを再実行する

Gateway全体の設定をやり直す場合は、Gatewayのターミナルで`cd ~/projects/omk`を実行し、上の[手順3](#3-セットアップを再実行する)へ進みます。特定の機能だけを再設定する場合は、次の節にある専用コマンドを使います。

## 個別サービスを再設定する

標準Gatewayでは以下のサービスは導入済みです。計測機器の追加は[「BLEセンサを追加する」](ble-sensor-setup.md)または[「Bルートでスマートメーターを追加する」](broute-setup.md)を参照してください。以下は旧構成への導入や個別の再設定用で、Gateway自身のインターネット接続が必要です。

### BLEセンサ・OMK Nodeを使う

BLE Sensor Managerを導入します。

```bash
cd ~/projects/omk
./scripts/setup-ble-sensor-manager.sh
```

完了したら、[「BLEセンサを追加する」](ble-sensor-setup.md#bleセンサを探索して登録する)か、[「3-2. SEN66 Nodeをセットアップする」](esp32-node-setup.md)の登録手順を参照してください。センサの接続・設置は[「OMK Nodeで計測範囲を拡張する」](node-and-sensors.md)、SEN66の組み立ては[「3-1. SEN66 Nodeの組み立て」](sen66-node-assembly.md)を参照してください。

### Bルートを使う

RS-WSUHA-PをGatewayへ接続します。AtomS3 LiteなどほかのUSBシリアル機器は、セットアップ中だけ取り外してください。Onyxは外部通信のため接続したままにします。

```bash
cd ~/projects/omk
./scripts/setup-broute-meter.sh
```

`Use this FT230X serial adapter as RS-WSUHA-P? ... [y/N]`と表示されたら、接続した実物がRS-WSUHA-Pであることを確かめて`y`を入力します。完了したら、[「Bルートでスマートメーターを追加する」](broute-setup.md#2-bルートidパスワードを登録する)でBルートIDとパスワードを入力します。

### SORACOM Onyxを追加・再設定する

初めてOnyxを追加する場合は、設定中のインターネット接続としてGatewayを有線LANなどへ接続します。SIMは利用開始済みのSORACOM Air SIMを使います。

SIMを装着する場合は、OnyxをUSBから外し、本体の表示に合わせてSIMの金属端子の向きを確かめて挿入します。その後、GatewayへUSB接続してください。接続済みのOnyxを再設定する場合は、そのまま次のコマンドを実行できます。ほかのUSBモデムは、セットアップ中だけ取り外します。

通常のSORACOM Air SIMでは、次のコマンドでAPNに`soracom.io`が使われます。plan-DUのSIMを使う場合だけ、末尾に`--apn du.soracom.io`を付けます。

```bash
cd ~/projects/omk
./scripts/setup-soracom-onyx.sh
```

既存のOnyxを再設定する場合も同じコマンドを使います。すでに接続中の場合は、その接続を保持します。APNを変更する目的の再設定では、その指定だけで既存接続が切り替わるとは限らないため、接続結果を確認してください。

完了したら、SORACOMユーザーコンソールで対象SIMのセッション状態が「オンライン」になっていることを見ます。接続できない場合は[「トラブルシューティング」](troubleshooting.md#onyxで外部通信できない)を参照してください。

## 状態を確認する

GatewayのTouch Display 2でDashboardを操作します。別の表示端末を使う場合は[「標準構成以外・高度な構成」](advanced-configuration.md#pcやタブレット等からdashboardを使う場合)を参照してください。

| 対象 | 確認する場所と表示 |
| --- | --- |
| OMK AP | PCのWi-Fi一覧に、設定済みのSSIDが表示される。SSIDはDashboardの「OMKアクセスポイント参照」で見られる。 |
| Dashboard | 表示画面が開く |
| 登録済みBLEセンサ | 「機器管理」の「登録済みセンサ」で、対象の「最終受信」が更新され、状態が「正常」になる |
| OMK Node | 「機器管理」で、無線接続しているNodeの「接続」が「オンライン」になる。SEN66を使う場合は、表示画面に計測値が表示される。 |
| Bルート | 「Bルート設定」の「接続状態」が「接続済み」になる |

機器の行は、使用しているものだけ確認します。表示が戻らない場合は、[「トラブルシューティング」](troubleshooting.md)の該当する症状へ進んでください。サービスやログによる詳細な診断は、[「開発ガイド」](../developer/development.md#gatewayでの確認)を参照してください。

## 再起動・シャットダウンする

Dashboardの管理メニューから「システム操作」を開き、「再起動」または「シャットダウン」を選んで、確認画面の「実行」を押します。この間は計測も停止します。

電源を外す場合は、シャットダウンが完了してから外してください。シャットダウン後は、電源を入れ直すまで利用できません。画面操作は[「4-1. Dashboardの使い方」](dashboard.md#gatewayを再起動シャットダウンする)も参照してください。

## 構築後のGatewayへ接続する

Gatewayでは、USBキーボードを接続して`Ctrl+Alt+T`でターミナルを開きます。PCから操作するときは次の方法があります。

- **近くのPC**：Touch Display 2の「OMKアクセスポイント参照」で接続情報を表示し、PCをOMK APへ接続します。PowerShellで`ssh omkdev@192.168.50.1`を実行します。Gatewayの外部通信はOnyxを使います。
- **SORACOM Napterで遠隔接続**：PCをインターネットへ接続し、SORACOMユーザーコンソールで対象SIMが「オンライン」であることを確認します。Napterのオンデマンドリモートアクセスを開始し、[SORACOM公式のSSH接続手順](https://users.soracom.io/ja-jp/docs/napter/login-with-ssh/)の「PCのターミナルを使ってSSH接続する」に従います。接続先・ポートはNapterの表示値、ユーザー名は`omkdev`、パスワードはImagerで設定したものです。

## 保守完了の確認

更新・再設定が終わり、Dashboardと利用中の機器の値が更新されることを確認できたら保守完了です。
