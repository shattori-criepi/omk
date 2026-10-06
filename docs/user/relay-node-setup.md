# 中継専用OMK Nodeを追加する

SEN66の計測を確認した後、Gatewayから離れた場所へ計測範囲を広げたい場合に使う手順です。[「OMK Nodeで計測範囲を拡張する」](node-and-sensors.md)で、BLE受信とNode間のMesh中継の違い、設置場所を確認してください。

中継専用Nodeは、SEN66を接続せずに使うAtomS3 Liteです。SEN66 Nodeと同じファームウェアでBLE中継とMesh中継を行えます。中継専用のファームウェアやLogical IDの登録は不要です。

## 必要な部品

| 区分 | 部品 | 参考製品・実使用例 | 用途・選定条件 |
| --- | --- | --- | --- |
| 必須 | 小型端末 | [M5Stack AtomS3 Lite](https://www.switch-science.com/products/8778) | BLEセンサや他のNodeの通信を中継 |
| 必須 | USB電源 | USB-A出力のACアダプター（5V、1A以上を目安） | 設置場所での常時給電 |
| どちらか必須 | 給電用の接続 | [Type-Cオス USB-Aオス変換アダプタ](https://www.amazon.co.jp/dp/B0CKVP8DZ8)、またはUSB-A → USB-Cケーブル | USB電源とAtomS3 Liteを接続 |
| 初期設定時に必須 | USBケーブル | データ通信・給電対応のUSB-A → USB-Cケーブル | Gatewayとの接続用。上の給電用ケーブルがデータ通信対応なら兼用できます。 |

## この手順の前に

- Gatewayのセットアップと初回のSEN66計測確認を済ませ、OMK APとDashboardを使える状態にします。
- 新品・未セットアップのAtomS3 Liteを用意します。使用済みNodeの接続先を変える場合は[「OMK Nodeを更新・再設定する」](node-maintenance.md#既存nodeを再セットアップする)を参照してください。
- BLEセンサの初回登録はGatewayの近くで行います。Node経由でのみ受信している未登録センサは探索候補に表示されません。登録操作は[「BLEセンサを追加する」](ble-sensor-setup.md#bleセンサを探索して登録する)を参照してください。

## 1. GatewayへUSB接続する

SEN66を接続していないAtomS3 Liteを、データ通信対応のUSBケーブルでGatewayへ接続します。ほかのAtomS3 Liteや同種の開発ボードは、セットアップ中だけGatewayから取り外します。OnyxとRS-WSUHA-Pは接続したままで構いません。

## 2. Dashboardからセットアップする

1. Touch Display 2のDashboardで「管理メニュー → 機器管理」を開きます。
2. 「接続: USB接続」のカードに「USB接続されたNode候補」と「OMK Nodeをセットアップ」が表示されることを確認します。
3. 接続した実物を確かめ、「未セットアップのAtomS3 Liteであることを確認しました」にチェックを入れます。初回設定の再開時には、この確認欄が表示されないことがあります。
4. 「OMK Nodeをセットアップ」を押します。ファームウェアとOMK APのWi-Fi設定が書き込まれます。
5. USB接続と給電を維持し、「セットアップ完了」と表示されるまで待ちます。

既存Nodeの再セットアップが表示された場合は、[「OMK Nodeを更新・再設定する」](node-maintenance.md)を参照してください。失敗した場合は[「トラブルシューティング」](troubleshooting.md#nodeセットアップを診断する)で表示内容を確認します。

## 3. 設置して通信を確認する

1. GatewayからAtomS3 Liteを取り外し、設置場所のUSB電源へ接続します。常時給電してください。
2. Dashboardの「機器管理」で、対象Nodeの「接続」が「オンライン」になることを確認します。「Wi-Fi設定済み」「接続センサ: 未検出」の表示で使えます。
3. BLE中継に使う場合は、登録済みセンサの「最終受信」が更新されることを確認します。Mesh中継に使う場合は、その先のNodeが「オンライン」になり、計測値が更新されることを確認します。

通信が続かない場合は、GatewayやほかのNode、対象のBLEセンサに近づけて配置を調整します。設置後も対象の計測値や受信時刻が更新されれば、追加したNodeでの通信を確認できています。
