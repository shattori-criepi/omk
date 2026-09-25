# OMKを使い始める

OMKは、データを受信・保存するGateway（Raspberry Pi）と、計測するセンサ・機器を組み合わせて使います。計測値や機器の状態は、Dashboardという画面で確認します。用語の意味は[用語集](glossary.md)も参照してください。

## 推奨構成

まず、Gateway用に次を用意します。

- Raspberry Pi 4、電源、microSDカードとカードリーダー
- Raspberry Pi Touch Display 2（7インチ）
- SORACOM OnyxとSORACOM Air SIM（Gatewayのインターネット接続用）
- 初期設定に使うWindows PC
- インターネットに接続できる既存Wi-Fi

部品の準備例は[OMKの参考パーツ構成](parts-list.md)を参照してください。ディスプレイなし・Onyxなしなどの構成については、[Gatewayセットアップの補足](gateway-setup.md#標準構成以外で使う場合)を参照してください。

## 必要に応じて追加する機器

計測したい項目に合わせて、次のような機器を追加します。

- SEN66やBLE（近距離無線通信）センサなどの計測機器
- スマートメーターの電力データを取得するBルートUSBアダプタ
- センサの接続や通信の中継に使う小型端末、OMK Node（AtomS3 Lite）

対応機種と計測できる項目は[対応センサ・機器と取得データ](supported-devices.md)を参照してください。

## OMK APとは

OMK APは、Gatewayが提供するOMK用Wi-Fiです。OMK NodeやPCをGatewayへ接続するために使います。**OMK APへ接続してもインターネットへは接続できません。**

## 導入の流れ

1. [Gatewayセットアップ](gateway-setup.md)に沿ってGatewayを構築します。
2. 必要なセンサ・機器を追加します。OMK Nodeを使う場合は、[OMK Nodeの役割と設置](node-and-sensors.md)を読んでから[AtomS3 LiteへのOMK Node導入](esp32-node-setup.md)へ進みます。BLEセンサは[Dashboardで登録](dashboard.md#bleセンサを探索して登録する)し、Bルートは[専用の設定手順](gateway-setup.md#8-bルートを使用する場合は設定する)を参照してください。
3. [Dashboardの使い方](dashboard.md)を参照し、登録した機器の計測値や状態が表示されることを確認します。
4. 構築後の更新や機能追加は[Gatewayの更新・保守](gateway-maintenance.md)を参照してください。

うまく進まない場合は[トラブルシューティング](troubleshooting.md)を参照してください。
