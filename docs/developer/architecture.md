# アーキテクチャ

この文書は現行OMK実装の構成を説明します。利用手順は[利用者向け文書](../user/getting-started.md)、設計判断の採否履歴は[decisions](../decisions/)、原則は[設計原則](../design-principles.md)を参照してください。

## 全体構成

```text
センサ / Bルート / パワコン / BLE / ESP32 Node
                     ↓ MQTT
Mosquitto → sensor-collector → JSONL → data-transformer → Parquet → Dashboard
     └── harvest-uploader → 1分集約 → SORACOM Harvest
```

GatewayはRaspberry Pi 4/5、64-bit Raspberry Pi OS上で動作します。Docker ComposeにはMosquitto、sensor-collector、Dashboard、harvest-uploaderを置き、OS権限や物理デバイスに強く依存するものはhost systemdで動作します。

## Gatewayとhost service

- `omk-system-manager.service`: DashboardからのBルート設定、Gateway reboot/shutdown、OMK AP credential参照を限定APIで仲介する。Bルート専用ではない。
- `omk-broute-meter.service`: USBシリアル、PANA認証を要するBルート計測。
- `omk-ble-sensor-manager.service`: BlueZによるBLE探索・登録・受信。
- `omk-data-transformer.timer`: JSONLを日次のParquetへ変換。
- `omk-dashboard-kiosk.service`: GUI session内の任意のChromium kiosk。
- `omk-ichijo-energy-node.service`: Gatewayとは別Raspberry Pi上で動かす、現行一条設備向けパワコン連携。

system-managerはDashboardの`host.docker.internal`経由の要求を認証し、ブラウザへhost権限や秘密情報を渡しません。

## NodeとBLE

共通ESP32 NodeはUSB Serial/JTAG Provisioning、Wi-Fi/MQTT、SEN66などのI2Cセンサ、BLE relayを扱います。SEN66が未接続でもBLE relayとして利用できます。初回登録に物理ボタン操作は必要ありません。

BLE environment sensorはGateway direct BLEを主系とし、ESP32 Node relayをfallbackにします。directが約30秒途絶するとrelayを採用し、direct復帰時はdirectへ戻ります。advertisement単位の厳密dedupより、1分集約用途の経路冗長化を優先します。詳細な入力topicと選択理由は[データ経路とMQTT](data-and-mqtt.md)および[decision](../decisions/ble-direct-relay-route-selection.md)を参照してください。

Node間Wi-Fi中継による範囲拡張は今後の方針であり、具体的な方式は未確定です。ESP-MESH等を現行仕様として扱いません。

## 保存・表示・送信

`sensor-collector`は`omk/#`を購読してJSONLへ一次保存し、generic latest storeと互換latest JSONを更新します。Dashboardは最新値とParquetを使い、表示設定を`data/dashboard/settings.json`へ保存します。`harvest-uploader`はJSONLやParquetを読まず、MQTTを別clientとして直接購読し1分集約します。

MQTT topic、payload、保存形式は[データ経路とMQTT](data-and-mqtt.md)を正本とします。OMK APの隔離とDocker公開ポートの扱いは[ネットワーク設計](networking.md)を参照してください。

## セキュリティ境界

OMK AP clientはGatewayのローカルサービスと公開MQTT/Dashboardへ接続できますが、Internetへは転送されません。MQTTは現在OMK専用LAN内の匿名・平文接続です。認証、ACL、TLSは未実装の将来課題です。
