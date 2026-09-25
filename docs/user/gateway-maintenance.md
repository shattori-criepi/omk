# Gatewayの更新・保守

構築済みGatewayの更新、機能追加、再設定、状態確認を説明します。初めて構築する場合は[Gatewayセットアップ](gateway-setup.md)、問題が起きた場合は[トラブルシューティング](troubleshooting.md)を参照してください。

## OMKを更新する

更新にはGateway自身のインターネット接続が必要です。標準構成ではSORACOM Onyxを使います。Onyxを使わない場合は、有線LANなどの接続を用意してください。OMK APはインターネット接続用ではありません。

更新中はDashboardや計測が一時的に停止します。通常の更新やセットアップの再実行では、既存の設定と計測データは保持されます。

### 1. Gatewayへ接続する

Gatewayへの接続・操作方法は、[GatewayセットアップのAP切替後の操作方法](gateway-setup.md#ap切替後の操作方法)を参照してください。

以下のコマンドはGateway上で実行してください。

```bash
cd ~/projects/omk
git status --short
```

何も表示されなければ、次へ進みます。ファイル名が表示された場合は、手元の変更や追加ファイルがあるため、ここで更新を止めてください。この手順では、変更のない状態からの更新を扱います。

### 2. 最新版を取得する

```bash
git pull --ff-only
```

取得が完了したら次へ進みます。`Already up to date.`と表示された場合は、すでに最新版です。

### 3. セットアップを再実行する

標準構成のSORACOM OnyxとBLE Sensor Managerを使っている場合は、次のコマンドで更新を反映します。**通常の更新では`--with-base`を付けません。**

実行前に、使用中の構成に合わせてオプションを調整してください。

| 使用している構成 | 下のコマンドからの変更 |
| --- | --- |
| Bルートを使う | `--with-broute`を追加する。RS-WSUHA-PをGatewayへ接続しておく。 |
| Onyxでplan-DUのSIMを使う | コマンドの先頭に`SORACOM_APN=du.soracom.io`を付ける |
| Onyxを使わない | `--with-soracom`を外す |
| BLEセンサとOMK Nodeをどちらも使わない | `--with-ble`を外す |
| GatewayのディスプレイにDashboardを自動表示しない | `--no-kiosk`を追加する |

```bash
./scripts/setup-omk-gateway.sh --with-soracom --with-ble
```

使用中の機能のオプションを外すと、その機能は更新されません。

OMK APの確認メッセージが表示されたら、[OMK APへの切り替え](gateway-setup.md#6-omk-apへの切り替え)と同じように操作します。最後のAP有効化でSSHが切断される場合がありますが、セットアップは切断後も完了まで進みます。

### 4. 更新後の動作を確認する

セットアップが完了したら、[状態を確認する](#状態を確認する)の表に沿って、Dashboardと使用中の機器の表示を見ます。

Gatewayを更新しても、設置済みOMK Nodeのファームウェアは自動更新されません。Nodeの更新は[AtomS3 LiteへのOMK Node導入](esp32-node-setup.md#firmwareを更新する)を参照してください。

## セットアップを再実行する

Gateway全体の設定をやり直す場合は、Gatewayのターミナルで`cd ~/projects/omk`を実行し、上の[手順3](#3-セットアップを再実行する)へ進みます。特定の機能だけを再設定する場合は、次の節にある専用コマンドを使います。

## 構築後に機能を追加する

以下のコマンドもGateway上で実行します。追加に必要なファイルを取得するため、インターネットへ接続しておいてください。

### BLEセンサ・OMK Nodeを使う

BLE Sensor Managerを導入します。

```bash
cd ~/projects/omk
./scripts/setup-ble-sensor-manager.sh
```

完了したら、[DashboardでBLEセンサを登録する](dashboard.md#bleセンサを探索して登録する)か、[AtomS3 LiteをOMK Nodeとして準備する](esp32-node-setup.md)手順へ進みます。センサの接続・設置は[OMK Nodeの役割と設置](node-and-sensors.md)、SEN66の組み立ては[SEN66 Nodeの組み立て](sen66-node-assembly.md)を参照してください。

### Bルートを使う

RS-WSUHA-PをGatewayへ接続します。ほかのUSBシリアル機器は、セットアップ中だけ取り外してください。

```bash
cd ~/projects/omk
./scripts/setup-broute-meter.sh
```

`Use this FT230X serial adapter as RS-WSUHA-P? ... [y/N]`と表示されたら、`y`を入力します。完了したら、[Dashboardの「Bルート設定」](dashboard.md#bルートを設定する)でBルートIDとパスワードを入力します。

### SORACOM Onyxを追加・再設定する

初めてOnyxを追加する場合は、設定中のインターネット接続としてGatewayを有線LANなどへ接続します。SIMは利用開始済みのSORACOM Air SIMを使います。

SIMを装着する場合は、OnyxをUSBから外し、本体の表示に合わせてSIMの金属端子の向きを確かめて挿入します。その後、GatewayへUSB接続してください。接続済みのOnyxを再設定する場合は、そのまま次のコマンドを実行できます。ほかのUSBモデムは、セットアップ中だけ取り外します。

通常のSORACOM Air SIMでは、次のコマンドでAPNに`soracom.io`が使われます。plan-DUのSIMを使う場合だけ、末尾に`--apn du.soracom.io`を付けます。

```bash
cd ~/projects/omk
./scripts/setup-soracom-onyx.sh
```

既存のOnyxを再設定する場合も同じコマンドを使います。すでに接続中の場合は、その接続を保持します。

完了したら、SORACOMユーザーコンソールで対象SIMのセッション状態が「オンライン」になっていることを見ます。接続できない場合は[Onyxで外部通信できない場合の対処](troubleshooting.md#onyxで外部通信できない)を参照してください。

## 状態を確認する

PCで確認する場合はOMK APへ接続し、ブラウザで`http://192.168.50.1:8000/`を開きます。GatewayのディスプレイでもDashboardを操作できます。

| 対象 | 確認する場所と表示 |
| --- | --- |
| OMK AP | PCのWi-Fi一覧に、設定済みのSSIDが表示される。SSIDはDashboardの「OMKアクセスポイント参照」で見られる。 |
| Dashboard | 表示画面が開く |
| 登録済みBLEセンサ | 「機器管理」の「登録済みセンサ」で、対象の「最終受信」が更新され、状態が「正常」になる |
| OMK Node | 「機器管理」で、無線接続しているNodeの「接続」が「オンライン」になる。SEN66を使う場合は、表示画面に計測値が表示される。 |
| Bルート | 「Bルート設定」の「接続状態」が「接続済み」になる |

機器の行は、使用しているものだけ確認します。表示が戻らない場合は、[トラブルシューティング](troubleshooting.md)の該当する症状へ進んでください。サービスやログによる詳細な診断は、[開発ガイドのGatewayでの確認](../developer/development.md#gatewayでの確認)を参照してください。

## 再起動・シャットダウンする

Dashboardの管理メニューから「システム操作」を開き、「再起動」または「シャットダウン」を選んで、確認画面の「実行」を押します。この間は計測も停止します。

電源を外す場合は、シャットダウンが完了してから外してください。シャットダウン後は、電源を入れ直すまで利用できません。画面操作は[Dashboardの使い方](dashboard.md#gatewayを再起動シャットダウンする)も参照してください。
