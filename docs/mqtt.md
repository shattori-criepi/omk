# MQTT仕様

MQTTは、各データ取得処理をRaspberry Piへ集約するLAN内の内部データバスである。ESP32などのセンサノードはOMK専用Wi-Fi経由でRaspberry Pi上のMosquittoへ送信し、クラウドへ直接接続しない。Brokerは初期構成では`192.168.50.1:1883`で待ち受け、Composeのポート公開もこのAP側IPに限定する。

## トピックとdevice_id

`device_id`は各ノードを識別する設定値である。トピックは`omk/<device_id>/<data_type>`を基本とし、`data_type`には測定種別または`status`を置く。現時点では`network_config.h`の固定値を使い、将来MACアドレス由来のIDへ変更できるよう、トピックとpayloadで直接固定しない。

| 用途 | トピック | QoS | retain |
| --- | --- | --- | --- |
| SEN66測定値 | `omk/<device_id>/sen66` | 0 | false |
| 接続状態 | `omk/<device_id>/status` | 0 | true |
| BLE環境センサ | `omk/<sensor_id>/environment` | 0 | false |
| BLE人感状態変化 | `omk/<sensor_id>/motion` | 0 | false |
| BLE開閉状態変化 | `omk/<sensor_id>/contact` | 0 | false |
| BLEプラグ電力 | `omk/<sensor_id>/power` | 0 | false |

初期ノードの例は`omk/sen66-001/sen66`と`omk/sen66-001/status`である。

## Payload

測定値はSEN66の取得成功時に送信する。未取得値は非標準の`NaN`ではなくJSONの`null`にする。ESP32はまだ時刻同期をしていないため`measured_at`を含めず、受信時刻の付与またはNTP導入は後続課題とする。

```json
{"device_id":"sen66-001","uptime_ms":123456,"pm1_0_ug_m3":4.1,"pm2_5_ug_m3":6.8,"pm4_0_ug_m3":8.2,"pm10_0_ug_m3":10.5,"relative_humidity_percent":48.2,"temperature_celsius":25.3,"voc_index":92.0,"nox_index":null,"co2_ppm":612.0}
```

MQTT接続直後はretain付きで次を送信する。

```json
{"device_id":"sen66-001","status":"online","firmware_name":"omk-sen66-node","firmware_version":"0.1.0"}
```

接続時にはLast Willを設定し、予期しない切断ではretain付きで次を送信する。

```json
{"device_id":"sen66-001","status":"offline"}
```

## 汎用JSONL収集

`sensor-collector`は`omk/#`をQoS 0で購読し、測定値と`omk/<device_id>/status`の両方を収集する。collectorはセンサ機種、`device_id`、測定項目の意味を解釈せず、受信したpayloadをトップレベルへ展開しない。

Raspberry Pi側でAsia/Tokyoの受信時刻をミリ秒付きISO 8601形式で付与し、`data/sensors/YYYY/MM/DD.jsonl`へ1メッセージ1行で追記する。通常のJSON payloadの共通構造は次のとおりである。

```json
{"received_at":"2026-07-30T10:54:12.123+09:00","topic":"omk/sen66-001/sen66","qos":0,"retain":false,"payload":{"device_id":"sen66-001"}}
```

JSONとして解析できないUTF-8 payloadは`payload_raw`と`payload_parse_error`を記録する。UTF-8でないpayloadは`payload_base64`と`payload_encoding: "base64"`で保持し、メッセージを破棄しない。CSVは一次保存形式ではなく、必要に応じてこのJSONLから後段で生成する。

## latest状態キャッシュ

JSONL保存に成功した正常JSON payloadのうち、`omk/<device_id>/power`、`sen66`、`power-flow`は、それぞれ`data/latest/broute_power.json`、`sen66.json`、`ichijo_power_flow.json`へ更新される。collectorは同一ディレクトリの一時ファイルをatomic置換するため、dashboardは読取り途中のJSONを参照しない。latest更新が失敗してもJSONL収集は継続する。詳細な保存条件と権限は[sensor-collector README](../services/sensor-collector/README.md)を参照する。

## BLEセンサ

SwitchBot等のBLE受信は Dashboard ではなく Raspberry Pi ホスト上の
`omk-ble-sensor-manager` が行う。登録の物理 `device_key` と論理
`sensor_id` を分離し、MQTT payload の `device_id` には後者だけを使用する。
environment payload は `device_id`、`measured_at`、`quality`、利用可能な `temperature_c`、
`relative_humidity_percent`、`co2_ppm`、`battery_percent` を含む。collector
は従来通り型を解釈せず JSONL に保存する。

Gateway direct BLEとESP32 Node relayを併用するenvironment sensorでは、Nodeは論理
`sensor_id`ではなく`switchbot:<12桁lowercase hex>`の物理`device_key`を
`omk-relay/<relay_node_id>/ble/environment`へpublishする。この内部topicは
`sensor-collector`と`harvest-uploader`の`omk/#`購読対象外である。BLE Sensor Managerが
registryで解決した後だけ、通常の`omk/<sensor_id>/environment`へcanonical messageをpublishする。
direct/relayの経路選択仕様とpayload例は
[BLE direct / ESP32 Node relayの経路選択](decisions/ble-direct-relay-route-selection.md)を参照する。

BLE advertisement は常時受信し、runtimeのlatest値、RSSI、受信時刻は広告ごとに
更新する。environmentは初回の正常値を即時publishし、以後は`device_key`ごとに
最短10秒間隔でpublishする。Motion Sensorは状態変化時に即時publishし、同一状態も
最短10秒間隔で `{"device_id":"motion-001","measured_at":"...","motion_state":1}`
の形でpublishする。`motion_state`は0=不在、1=検知である。

Contact Sensorも状態変化時に即時publishし、同一状態も最短10秒間隔で
`{"device_id":"contact-001","measured_at":"...","contact_state":1}` を
publishする。`contact_state`は0=閉、1=開で、公式のtimeout-not-closeも開（1）として
正規化する。各rate limitはmonotonic clockを使い、`enabled=false`ではBLE observationと
runtime更新を継続する一方、MQTT publishは停止する。したがってJSONLへのSwitchBot
一次保存は厳密な固定周期ではなく、通常は最短約10秒間隔となる。

Plug Miniは`{"device_id":"plug-001","measured_at":"...","power_w":173.2,
"switch_state":1}`を`omk/<sensor_id>/power`へpublishする。`switch_state`は
0=OFF、1=ONで、初回は即時、同一状態は最短10秒間隔、状態変化は即時publishする。
Pi実機では`omk/plug-001/power`がsensor-collectorの日次JSONLへ保存され、`power_w`と
`switch_state`がpayloadのまま記録されることを確認した。広告時刻に依存するため厳密な
10秒固定ではないが、通常は最短約10秒間隔で保存される。

## Bルート接続

Bルート通信はUSBシリアル、OS権限、認証、PANA通信に依存するため、当面はホスト上のsystemdサービスで実行し、コンテナ化しない。Bルート値は`omk/<device_id>/power`などへpublishされ、collectorのJSONL保存とlatest状態キャッシュへ反映される。既存の専用保存の扱いは、MQTT経由の保存を並行検証した後に判断する。

## セキュリティと運用

現在のMosquitto設定はOMK専用LANでの初期確認用であり、匿名・平文接続である。将来はユーザー名・パスワード、ACL、TLSを追加する。MQTTポートをSORACOM側または他のホストインターフェースに公開してはならない。
