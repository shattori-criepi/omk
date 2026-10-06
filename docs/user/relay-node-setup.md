# 中継専用OMK Nodeをセットアップする

[「OMK Nodeで計測範囲を拡張する」](node-and-sensors.md)で中継専用Nodeが必要と判断した方の作業手順です。AtomS3 Liteを準備し、Gatewayで設定してから設置・通信確認を行います。

## この手順の前に

- Gatewayのセットアップを済ませ、OMK APとTouch Display 2のDashboardを使える状態にします。
- 通信確認に使うBLEセンサは、[「BLEセンサを追加する」](ble-sensor-setup.md)に従って登録を済ませます。SEN66 Nodeを使って確認する場合は、そのNodeの設定・登録を済ませます。

## 1. AtomS3 Liteを準備する

| 部品 | 参考製品・実使用例 | 準備するもの |
| --- | --- | --- |
| 小型端末 | [M5Stack AtomS3 Lite](https://www.switch-science.com/products/8778) | 新品・未セットアップのもの。センサは接続しません。 |
| USB電源 | USB-A出力のACアダプター（5V、1A以上を目安） | 設置場所での常時給電用 |
| USBケーブル | データ通信・給電対応のUSB-A → USB-Cケーブル | Gatewayでの設定と設置後の給電に使用 |
| 給電用アダプタ（任意） | [Type-Cオス USB-Aオス変換アダプタ](https://www.amazon.co.jp/dp/B0CKVP8DZ8) | 設置後にUSBケーブルの代わりに使用する場合 |

使用済みNodeの接続先を変える場合は、[「OMK Nodeを更新・再設定する」](node-maintenance.md#既存nodeを再セットアップする)の手順を使ってください。

## 2. GatewayへUSB接続する

AtomS3 Liteをデータ通信対応のUSBケーブルでGatewayへ接続します。ほかのAtomS3 Liteや同種の開発ボードは、セットアップ中だけGatewayから取り外します。OnyxとRS-WSUHA-Pは接続したままで構いません。

## 3. Dashboardからセットアップする

1. Touch Display 2のDashboardで「管理メニュー → 機器管理」を開きます。
2. 「接続: USB接続」のカードに「USB接続されたNode候補」と「OMK Nodeをセットアップ」が表示されることを確認します。
3. 接続した実物を確かめ、「未セットアップのAtomS3 Liteであることを確認しました」にチェックを入れます。初回設定の再開時には、この確認欄が表示されないことがあります。
4. 「OMK Nodeをセットアップ」を押します。
5. USB接続と給電を維持し、「セットアップ完了」と表示されるまで待ちます。Logical IDの登録は行いません。

既存Nodeの再セットアップが表示された場合は、[「OMK Nodeを更新・再設定する」](node-maintenance.md)を参照してください。失敗した場合は[「トラブルシューティング」](troubleshooting.md#nodeセットアップを診断する)で表示内容を確認します。

## 4. 設置する

1. 設定したAtomS3 LiteをGatewayから取り外します。
2. 選んだ設置場所へ置き、USB電源に接続します。常時給電してください。

## 5. 通信を確認する

1. Dashboardの「機器管理」で、設置したNodeの「接続」が「オンライン」になることを確認します。「Wi-Fi設定済み」「接続センサ: 未検出」の表示で使えます。
2. 対象のBLEセンサを使う場合は、そのセンサの「最終受信」が更新されることを確認します。離れたSEN66 Nodeを使う場合は、そのNodeが「オンライン」になり、計測値が更新されることを確認します。
3. 更新が続かない場合は、設置したNodeをGatewayや対象機器に近づけ、位置を調整して再確認します。

設置後も対象の計測値や受信時刻が更新されれば、作業完了です。
