# OMK — おうちモニタキット

OMK（おうちモニタキット）は、住宅内の電力・環境・行動に関するデータを、必要なセンサを組み合わせて計測、表示、保存、外部送信するためのオープンなIoT基盤です。Raspberry PiをGatewayとして使い、住宅側ネットワークに依存せずに運用できる構成を目指します。

## できること

- Raspberry Pi 4/5上のGateway、OMK専用Wi-Fi AP、MQTTによるセンサ集約
- JSONL一次保存、Parquet変換、Dashboardでの瞬時値・履歴表示
- Bルート、BLEセンサ、ESP32 Node、SORACOM Harvest連携
- 任意のパワコン連携（現行実装は一条設備向け `ichijo-energy-node`）

```text
センサ / Bルート / パワコン / BLE
             ↓ MQTT
Gateway → sensor-collector → JSONL → Parquet → Dashboard
             └ harvest-uploader → SORACOM Harvest
```

## OMKを使う

新規導入の全体像と必要機材は[利用開始ガイド](docs/user/getting-started.md)を参照してください。新規Gatewayでは次が標準入口です。

```bash
./scripts/setup-omk-gateway.sh --with-base
```

既存Gatewayの再実行では、OS/Docker基盤を更新しないよう通常は`--with-base`を付けません。詳しくは[Gatewayセットアップ](docs/user/gateway-setup.md)を参照してください。

- [Nodeとセンサ](docs/user/node-and-sensors.md)
- [Dashboardの使い方](docs/user/dashboard.md)
- [利用者向けトラブルシューティング](docs/user/troubleshooting.md)

## OMKを開発する

- [アーキテクチャ](docs/developer/architecture.md)
- [開発環境・テスト](docs/developer/development.md)
- [ネットワーク設計](docs/developer/networking.md)
- [データ経路とMQTT](docs/developer/data-and-mqtt.md)
- [リポジトリ構成](docs/developer/repository-structure.md)

各サービスとfirmwareのREADMEは、単体の起動、設定、テスト、デバッグの正本です。

## 現在の対応範囲

- 正式対象: Raspberry Pi 4/5、64-bit Raspberry Pi OS
- Gateway: Docker Compose、host systemd、NetworkManager
- Node: 共通ESP32 firmware（SEN66計測、BLE relay、USB Serial/JTAG Provisioning）
- 任意機能: SORACOM Onyx、BLEセンサ、Bルート、GUI kiosk、パワコン連携

MQTTは現在、OMK専用LAN内の匿名・平文接続です。認証、ACL、TLSは将来課題です。

## 開発状況と履歴

現行仕様は`docs/user/`と`docs/developer/`を正本とします。再開発時の構想・工程記録は[history](docs/history/)に分離しており、現行仕様ではありません。設計採否の理由は[decisions](docs/decisions/)に残します。

ライセンス情報はリポジトリ内のライセンスファイルを参照してください。
