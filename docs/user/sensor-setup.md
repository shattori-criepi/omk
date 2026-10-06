# センサ・計測機器を追加する

[Gatewayセットアップ](gateway-setup.md)でOMK本体が完成したら、計測する機器を追加します。初めて製作する場合は、次のSEN66 Nodeの手順を上から順に進めてください。

## この手順の前に

- Touch Display 2でDashboardを開けることを確認します。
- GatewayのBLE機能とBルートサービスは導入済みで、OMK APを有効にした状態にします。
- [OMKの参考パーツ構成](parts-list.md#sen66-nodeを使う場合)のSEN66 Node用部品を手元にそろえます。AtomS3 LiteとSEN66は、まだGatewayやUSB電源へ接続しません。

## まずSEN66 Nodeを1台追加する

SEN66 NodeはOMKの推奨センサ構成ですが、OMKの利用に必須ではありません。初めて製作する場合は、動作確認も兼ねて1台製作することを推奨します。

1. **[SEN66 Nodeの組み立て](sen66-node-assembly.md)**で、給電していないAtomS3 LiteへSEN66を配線し、ケースへ収めます。蓋は開けておきます。
2. **[AtomS3 LiteをOMK Nodeとしてセットアップする](esp32-node-setup.md)**で、GatewayへUSB接続してファームウェアとOMK APの設定を書き込みます。続けてSEN66のLogical IDを登録し、Dashboardで計測値を確認します。
3. 同じページの案内に従って蓋を固定し、設置場所のUSB電源へつなぎ直します。Dashboardの値と最終更新が更新されれば、SEN66での計測開始まで完了です。

**次へ：[SEN66 Nodeの組み立て](sen66-node-assembly.md)**へ進んでください。以下は、SEN66の計測を確認した後で、ほかの機器を追加するときに使います。

## SwitchBot等のBLEセンサを追加する

BLEセンサはGateway自身で直接受信できます。まず対象センサの電源を入れてGatewayの近くへ置き、[DashboardでBLEセンサを探索・登録](dashboard.md#bleセンサを探索して登録する)します。対応機種は[対応センサ・機器と取得データ](supported-devices.md#switchbot対応機器)で確認できます。

登録後は設置場所へ戻し、「機器管理」で「最終受信」が更新されることを確認します。Gatewayから遠くて直接受信できないときは、近くのOMK Nodeが代わりにBLEの電波を受信し、データをGatewayへ送る「BLE中継」を使えます。SEN66 Nodeもこの中継を兼ねられます。

**初回登録はGatewayの近くで行ってください。** OMK Node経由でのみ受信している未登録のBLEセンサは探索候補に表示されません。設置範囲を広げる場合は、[OMK Nodeの役割と設置](node-and-sensors.md)を参照します。

## Bルートでスマートメーターを追加する

Gateway側のBルートサービスは、本体製作時に導入済みです。ここではRS-WSUHA-Pを接続し、電力会社から発行された情報を入力して計測を始めます。

### この手順の前に

- Bルートの利用申し込みを済ませ、電力会社から発行されたBルートIDとパスワードを用意します。
- **RS-WSUHA-PをGatewayのUSBへ接続します。** 接続済みのAtomS3 LiteはGatewayから取り外し、設置場所のUSB電源へ戻します。ほかのUSBシリアル機器も一時的に取り外し、設定対象を取り違えない状態にします。Onyxは外部通信に使うため接続したままにします。
- [AP切替後の操作方法](gateway-setup.md#ap切替後の操作方法)でGatewayを操作でき、Gateway自身がOnyx経由でインターネットへ接続できることを確認します。次のセットアップ再実行でもパッケージの取得が行われます。

1. Gateway上で次を実行します。アダプタ未接続で本体のサービス導入を済ませた場合も、接続した機器を設定するために再実行します。

   ```bash
   cd ~/projects/omk
   ./scripts/setup-broute-meter.sh
   ```

2. `Use this FT230X serial adapter as RS-WSUHA-P? ... [y/N]`と表示されたら、接続した実物がRS-WSUHA-Pであることを確認して`y`を入力します。既にアダプタの接続先を設定済みの場合、この確認は表示されません。
3. 完了まで待ちます。スクリプトがRS-WSUHA-Pの抜き差しを求めた場合は、そのアダプタだけを抜き、数秒待って同じUSB端子へ接続し直します。OnyxやGatewayの電源は外しません。
4. [DashboardのBルート設定](dashboard.md#bルートを設定する)でIDとパスワードを入力し、「保存して接続」を押します。
5. 「接続状態」が「接続済み」になり、Dashboardに電力データが表示されることを確認します。

セットアップスクリプトはBルートIDとパスワードを尋ねません。認証情報を入力するのは手順4のDashboardです。スクリプトが停止した場合は、表示内容を確認して[セットアップのトラブルシューティング](troubleshooting.md)へ進みます。計測できない場合は[Bルートの対処方法](troubleshooting.md#bルートのデータを取得できない)を参照してください。

## 追加のOMK Nodeで設置範囲を広げる

Gatewayから離れたBLEセンサを受信するときや、遠くのNodeからGatewayまでの通信を中継するときは、[OMK Nodeの役割と設置](node-and-sensors.md)で配置を確認してから、[中継専用Nodeを追加](esp32-node-setup.md#中継専用nodeを追加する)します。NodeごとにGatewayへUSB接続して設定し、設置場所ではUSB電源から常時給電します。

BLEセンサの電波をNodeが受ける「BLE中継」と、Node同士がGatewayまで通信をつなぐ「Mesh中継」は別の役割です。同じNodeで両方を行えます。

このページで機器追加の順序を確認しました。初回は**[SEN66 Nodeの組み立て](sen66-node-assembly.md)**へ進んでください。追加した機器の計測値を確認できたら、**[Dashboardの使い方](dashboard.md)**で日常の運用へ進みます。
