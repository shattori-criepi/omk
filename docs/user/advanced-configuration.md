# 6-3. 標準構成以外・高度な構成

[「1-1. OMK導入ガイド」](getting-started.md)の標準構成を理解した後で、表示端末や外部通信を変更する際の参照ページです。以下の標準構成以外の組み合わせは、OMKでは実機で動作確認していません。

## PCやタブレット等からDashboardを使う場合

[「4-1. Dashboardの使い方」](dashboard.md#omk-apの接続情報を見る)に従い、Touch Display 2の「OMKアクセスポイント参照」でSSIDとパスワードを表示し、PCやタブレット等をそのWi-Fiへ接続します。ブラウザで`http://192.168.50.1:8000/`を開いてください。OMK APはGateway・Node・管理端末を結ぶローカルネットワークです。接続したPC等の外部通信はGateway経由では転送せず、外部通信が必要なGateway自身はOnyxを使います。

SSID・パスワードの入力の代わりに、端末が対応していれば接続用QRコードも使えます。

## 別のディスプレイ・ディスプレイなし

ディスプレイなしで構築する場合は、`setup-omk-gateway.sh`に`--no-kiosk`を追加します。別のディスプレイを使う場合も、標準kioskはTouch Display 2（DSI-1）の回転・タッチ設定を含むため、そのままの適合を前提にせず、表示環境を個別に調整してください。

画面からAP接続情報を見られない構成では、セットアップ中の`Show the OMK AP password now?`に`y`と答えてSSIDとパスワードを控え、AP切り替え後の接続先を準備します。PCをOMK APへ接続したら、`ssh omkdev@192.168.50.1`でGatewayを操作できます。AP接続情報は第三者へ共有しないでください。

## Onyxなし・有線LANで外部通信する

Onyxを使わない場合は、初回・再開の`setup-omk-gateway.sh`コマンドから`--with-soracom`を外します。OMK APは標準どおり有効にし、外部通信が必要なGatewayをインターネットに接続できる有線LANへ接続します。

初回セットアップ、AP切り替え後のBLE・Bルートサービス導入、更新にはダウンロードが必要です。APへ接続したPCからインターネットを共有できるわけではありません。Gateway自身の通信経路を確保してください。構築後のローカル計測・保存・表示は外部通信なしでも利用できます。

DashboardやMQTTは、標準ではGateway本体とOMK AP向けに公開されます。有線LANや家庭内Wi-Fiへ接続しただけでは、そのネットワーク上からDashboardへアクセスできません。

## plan-DUのSIMを使う

初回・再開のGatewayセットアップコマンドの先頭に`SORACOM_APN=du.soracom.io`を付けます。Onyxの個別設定なら次を使います。

```bash
./scripts/setup-soracom-onyx.sh --apn du.soracom.io
```

接続中のOnyxを保持する処理があるため、既存接続のAPN変更まで自動反映されるとは限りません。[「5-1. Gatewayソフトウェアの更新・保守」](gateway-maintenance.md#soracom-onyxを追加再設定する)と設定結果を確認してください。

## 家庭内Wi-Fiを使う構成について

標準のOMK APを家庭内Wi-Fiに置き換えるには、ネットワーク設計と設定の変更が必要です。Nodeの接続先SSIDだけを変える手順では完了しません。

- DashboardからのNode初回設定は、Gatewayの`omk-ap`プロファイルからWi-Fi情報を取得します。
- DashboardとMQTTのコンテナはIPv4ループバックにだけ公開され、OMK AP用の中継口は`192.168.50.1`・`wlan0`に制限されています。
- OMK APから外部インターフェースへの転送は遮断し、AP側のIPv6と上流DNS問い合わせも無効にしています。
- Raspberry Piの内蔵Wi-Fiは標準構成ではAP専用です。同じ内蔵Wi-Fiを既存Wi-Fiへの外部接続にも使う手順は提供していません。

変更を検討する場合は、[「Gatewayネットワーク設計」](../developer/networking.md)と[「OMK ESP32 Node」](../../firmware/esp32/omk-node/README.md)で、Gatewayへの到達性、provisioning、管理画面の公開範囲を確認してください。

## 高度なNode構成とクラウド連携

- [「6-1. OMK Nodeで計測範囲を拡張する」](node-and-sensors.md)：BLE中継とNode間Mesh中継の使い分け・詳細図。
- [「OMK ESP32 Node」](../../firmware/esp32/omk-node/README.md)：手動設定、Mesh、診断の技術情報。
- [「データ経路とMQTT仕様」](../developer/data-and-mqtt.md)：Gateway内部の受信・保存・送信。
- [「harvest-uploader」](../../services/harvest-uploader/README.md)：SORACOM Harvestへの送信。標準Composeではuploaderも起動するため、Harvest側の利用設定と送信動作はこの文書で確認します。

このページでは、標準構成から変更する箇所と実装上の制約を確認しました。
