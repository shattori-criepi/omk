# アーキテクチャ

この文書は現行OMK実装の構成を説明します。利用手順は[利用者向け文書](../user/getting-started.md)、設計判断の採否履歴は[decisions](../decisions/)、原則は[設計原則](../design-principles.md)を参照してください。

## 全体構成

```text
センサ / Bルート / パワコン / BLE / ESP32 Node
                     ↓ MQTT
Mosquitto → sensor-collector → JSONL → data-transformer → Parquet → Dashboard
                                                        └→ data-exporter → CSV/ZIP → USB
     └── harvest-uploader → 1分集約 → SORACOM Harvest
```

Gatewayの正式な動作確認対象はRaspberry Pi 4（64-bit Raspberry Pi OS）です。Docker ComposeにはMosquitto、sensor-collector、Dashboard、harvest-uploaderを置き、OS権限や物理デバイスに強く依存するものはhost systemdで動作します。

## Gatewayとhost service

- `omk-system-manager.service`: DashboardからのBルート設定、Gateway reboot/shutdown、OMK AP credential参照、USB Nodeセットアップ・再セットアップ、USBデータ書き出し開始を限定APIで仲介する。Bルート専用ではない。
- `data-exporter`: 既存のParquetを読み、CSV/ZIPを生成する。JSONLやParquetを変更せず、data-transformerも起動しない。
- `omk-broute-meter.service`: USBシリアル、PANA認証を要するBルート計測。
- `omk-ble-sensor-manager.service`: BlueZによるBLE探索・登録・受信。
- `omk-data-transformer.timer`: 1時間ごとにJST当日・前日のJSONLを日付partition済みParquetへ変換。
- `omk-dashboard-kiosk.service`: GUI session内で起動する標準のChromium kiosk。ディスプレイがない場合もunitと設定は標準setupで準備される。
- `omk-ichijo-energy-node.service`: Gatewayホストで動作する任意導入の、単一の検証profile向け住宅用PV・蓄電池・PCS ECHONET Lite連携。汎用PCS collectorではない。

DashboardのUSB書き出しはDashboard backendから認証済みsystem-managerを経由して固定の`omk-export-usb`を起動する。system-managerは非rootのまま、mount/unmountだけを限定root helperへ委譲する。helperはbrowserやsystem-managerから任意のdevice pathを受け取らず、自身で対応USBを再検出する。system-managerのfilesystem sandboxを維持するため、helperは`nsenter --mount=/proc/1/ns/mnt`でPID 1のhost mount namespace内に入り、USB検出、mount、unmountを実行する。

system-managerはDashboardの`host.docker.internal`経由の要求をBearer tokenで認証します。ブラウザへhost権限やこのtoken、保存済みBルート認証情報は渡しません。APのSSID/PSKは管理画面の明示的な「表示」操作でブラウザへ返すため、Dashboardの公開先をloopbackとOMK APに限定します。USB Node書込みはsystem-managerの非rootユーザーがdialout権限と固定版esptoolで行い、USBデータ書き出しのmount helperとは分離します。

## 限定的な動作確認済み住宅設備連携

**動作確認は、開発者宅の一条工務店住宅1戸・単一の設備構成に限られます。** 他の住宅・機種・構成での動作は未確認で、太陽光発電・蓄電池・PCSやECHONET Lite対応機器全般に対応する機能ではありません。

| 設備 | 接続方法 | 取得できる値・状態 | 対応上の注意 |
| --- | --- | --- | --- |
| 太陽光発電・蓄電池・PCS設備（上記の確認済み構成） | OMK APとは別に、対象設備と同じローカルネットワークへGatewayを接続 | 太陽光発電電力（W）、蓄電池残量（%）、蓄電池の充電電力・放電電力（W）、蓄電池の運転状態、買電電力・売電電力（W）、PCS交流出力電力（W）、住宅内消費電力（W） | 読み取り専用の任意連携で、OMKの標準構成には含まれません。住宅販売会社・機器メーカーの公式APIではありません。 |

実装と設定は[「ichijo-energy-node」](../../services/ichijo-energy-node/README.md)を参照してください。

## Node、ESP-WIFI-MESH、BLE

共通ESP32 NodeはUSB Serial/JTAG Provisioning、ESP-WIFI-MESH、Wi-Fi/IP/MQTT、SEN66などのI2Cセンサ、BLE relayを同時に扱います。SEN66が未接続でもBLE relayとして利用でき、SEN66とBLE relayを接続したNodeも同じfirmwareで動作します。production対象はAtomS3 Liteです。初回登録に物理ボタン操作は必要ありません。

保存済みGateway SSID/PSKを使ってESP-WIFI-MESHを自動形成する。topologyはrootを頂点に各childがparentへ接続するtreeであり、全Nodeが相互に直接通信する構成ではない。Nodeごとのparent、root、SSID、IPの手動指定は行わない。rootはGateway APへ通常STA接続し、childはMesh parent経由のinternal IP networkへDHCP接続する。rootはinternal subnet (`10.0.0.1/16`) のDHCP/DNS/NAPTを提供するため、root/childのどちらも通常TCPのMQTTで`192.168.50.1:1883`へ到達できる。

Nodeは計測・BLE relay・Mesh中継を兼ねる。配置と電波条件に応じてroot/parent/childは自動選択され、parentまたはrootを失うとMeshが再構成される。Gatewayは通常のAPとMosquittoだけを提供し、専用Mesh daemonや独自relay protocolを必要としない。市販Wi-Fi中継機は必須ではないが、到達性はNode配置、壁、階層、RSSIに依存する。AC電源を前提とし、電池駆動の省電力Meshとしては設計しない。

SwitchBot BLE sensorはGateway direct BLEを主系とし、ESP32 Node raw relayをfallbackにします。Nodeはactive scanでADVとScan Responseを取得してGateway decoderへ渡し、directが30秒以上途絶した場合だけrelayのデータを通常のsensor topicへpublishします。direct復帰時は同値でも最初のdirectから戻ります。BLEアドバタイズ単位の厳密dedupより、1分集約用途の経路冗長化を優先します。詳細な入力topicと選択理由は[データ経路とMQTT](data-and-mqtt.md)および[decision](../decisions/ble-direct-relay-route-selection.md)を参照してください。

Mesh診断status、credential導出、再構成時の観測上の注意は[ESP-WIFI-MESH Node networking decision](../decisions/esp-wifi-mesh-node-networking.md)に記載しています。

## 保存・表示・送信

`sensor-collector`は`omk/#`を購読してJSONLへ一次保存し、generic latest storeと互換latest JSONを更新します。Dashboardはlatest JSONから瞬時値を、DuckDBのメモリ内接続でParquetから日計を読み、表示設定を`data/dashboard/settings.json`へ保存します。`data-exporter`は保存済みParquetをCSV/ZIPとして書き出す別経路であり、CSVを一次保存形式として扱いません。`harvest-uploader`はJSONLやParquetを読まず、MQTTを別clientとして直接購読し1分集約します。確定した送信レコードは`data/harvest-uploader/queue.sqlite3`へ保存し、送信成功後に削除します。SQLiteはこのoutbox用で、Dashboardや計測履歴のDBではありません。

MQTT topic、payload、保存形式は[データ経路とMQTT](data-and-mqtt.md)で定義します。OMK APの隔離とDocker公開ポートの扱いは[ネットワーク設計](networking.md)を参照してください。

## セキュリティ境界

USB書き出しの権限境界は`Browser → Dashboard backend → authenticated system-manager → fixed USB export command → narrow root helper`です。Browserへsystem-manager token、root権限、device path指定能力を渡しません。

OMK AP clientはGatewayのローカルサービスと公開MQTT/Dashboardへ接続できますが、Internetへは転送されません。MQTTは現在OMK専用LAN内の匿名・平文接続です。認証、ACL、TLSは未実装の将来課題です。
