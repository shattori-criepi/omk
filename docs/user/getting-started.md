# OMKを使い始める

OMKは、Gatewayを中心に住宅内のセンサ、Bルート、BLE機器、任意の住宅用PV・蓄電池・PCS連携を集約し、Dashboardで確認する仕組みです。

## 主な機材

- Raspberry Pi 4または5（64-bit Raspberry Pi OS）と安定した電源・microSDカード
- 初期設定用のWindows PC、ネットワーク接続、SSH利用環境
- 必要に応じてESP32 Node、SEN66などのセンサ、BLEセンサ、BルートUSBアダプタ、表示用ディスプレイ
- SORACOM Onyxは、住宅ネットワークに依存しない外部通信が必要な場合だけ使用します

## 導入の流れ

1. Raspberry Pi OSを書き込み、Gatewayを準備する。
2. [Gatewayセットアップ](gateway-setup.md)でOMK AP、Dashboard、データ保存を有効にする。
3. [Nodeとセンサ](node-and-sensors.md)に従い、ESP32 NodeまたはBLEセンサを追加する。
4. [Dashboard](dashboard.md)でデータと管理状態を確認する。
5. 問題があれば[トラブルシューティング](troubleshooting.md)から確認する。

新規Gatewayの標準入口は次です。

```bash
./scripts/setup-omk-gateway.sh --with-base
```

OMK APに接続した端末はGatewayとその公開サービスへ接続できますが、Internetへは接続できません。これは意図した分離です。

SORACOM Onyxを使う場合は、Gatewayセットアップ後に[Onyxセットアップ](../soracom-onyx-setup.md)を実行します。この工程はoptionalです。
