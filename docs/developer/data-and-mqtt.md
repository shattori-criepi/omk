# データ経路とMQTT仕様

MQTTは、各データ取得処理をRaspberry Piへ集約するLAN内の内部データバスである。ESP32などのセンサノードはOMK専用Wi-Fi経由で`192.168.50.1:1883`へ送信し、クラウドへ直接接続しない。Composeはbrokerを`127.0.0.1:1883`だけへ公開し、`wlan0`にbindしたsystemd socket proxyがAP clientの接続を中継する。Gateway上のhost serviceは`127.0.0.1:1883`を使用する。

現行の主経路は、センサ・Bルート・パワコン・BLEからMQTT、`sensor-collector`、JSONL、`data-transformer`、processed Parquet、Dashboardです。`harvest-uploader`は別clientとしてMQTTを直接購読し、JSONLやParquetを読まずに1分集約してSORACOM Harvestへ送信します。

## トピックとdevice_id

`device_id`は各ノードを識別する設定値である。測定トピックは`omk/<device_id>/<data_type>`を基本とする。OMK Nodeの登録状態は、eFuse由来の`node_id`を使う専用のregistration topicで通知する。

| 用途 | トピック | QoS | retain |
| --- | --- | --- | --- |
| SEN66測定値 | `omk/<device_id>/sen66` | 0 | false |
| 論理device可用性 | `omk/<logical_id>/status` | 0 | true |
| OMK Node登録状態 | `omk/node/<node_id>/registration/status` | 1 | true |
| OMK Node Mesh診断 | `omk/node/<node_id>/status` | 0 | false |
| BLE環境センサ | `omk/<sensor_id>/environment` | 0 | false |
| BLE人感状態変化 | `omk/<sensor_id>/motion` | 0 | false |
| BLE開閉状態変化 | `omk/<sensor_id>/contact` | 0 | false |
| BLEプラグ電力 | `omk/<sensor_id>/power` | 0 | false |

SEN66測定トピックの例は`omk/sen66-001/sen66`である。

登録済みOMK NodeはMQTT接続時に`omk/<logical_id>/status`へretain付きで`online`を送信する。予期しないMQTT切断時は同一topicのretain付きLast Will`offline`がbrokerから送信される。これはlogical deviceのMQTT到達性であり、SEN66など個別測定値の鮮度は最新telemetryのGateway受信時刻で判定する。したがって復旧後の接続時`online`が古いretained `offline`を必ず上書きする。

## Payload

測定値はSEN66の取得成功時に送信する。未取得値は非標準の`NaN`ではなくJSONの`null`にする。ESP32は時刻同期をしていないため`measured_at`を含めず、sensor-collectorが付与するGateway受信時刻`received_at`を保存・集計に使用する。

```json
{"device_id":"sen66-001","uptime_ms":123456,"pm1_0_ug_m3":4.1,"pm2_5_ug_m3":6.8,"pm4_0_ug_m3":8.2,"pm10_0_ug_m3":10.5,"relative_humidity_percent":48.2,"temperature_celsius":25.3,"voc_index":92.0,"nox_index":null,"co2_ppm":612.0}
```

`harvest-uploader`はSEN66のtopic中のLogical IDをHarvest field prefixに使う。たとえば`omk/sen66-001/sen66`は`sen66-001_temperature_c`と`sen66-001_co2_ppm`を生成し、`omk/sen66-002/sen66`は別の`sen66-002_temperature_c`と`sen66-002_co2_ppm`を生成する。複数SEN66の1分平均は相互に混合しない。

OMK NodeはMQTT接続後と登録変更時に、QoS 1・retain付きでregistration statusを送信する。登録済みの場合はNodeのNVSから読んだ`logical_id`を含める。

```json
{"protocol_version":1,"node_id":"112233445566","registration_state":"registered","logical_id":"sen66-001","capabilities":3,"connected_sensors":["sen66"]}
```

新品Gatewayは、このstatusだけからNode registryのLogical IDを復元できる。旧Gatewayのregistryやretained ACKは不要。GatewayはIDの形式（1〜48文字のASCII英数字・`-`・`_`）と、他Nodeの確定済み・要求中IDとの重複を検証する。既存のprotocol version、capabilities、connected sensors、registration state、status受信時刻も保存する。

旧firmwareの`logical_id`を含まないregistered statusも受理し、Gatewayに保存済みのIDは消さない。明示的な`provisioned` statusは登録情報を解除する。firmwareはこの状態で`logical_id`を出力しない。Gatewayで解除済みの登録は、古いretained statusでは復活させない。

`registration/ack`は引き続き設定要求への応答であり、単なるMQTT再接続では送らない。GatewayからのID変更要求が進行中の場合は、従来どおり対応するACKで現在値を確定する。retained registration status自体はNodeのonline判定には使わない。

### Mesh診断status

ESP-WIFI-MESH Nodeは30秒ごとに非retainの診断statusを送信する。

```text
omk/node/<node_id>/status
```

```json
{"node_id":"112233445566","mesh_layer":2,"is_root":false,"parent_bssid":"02:00:00:00:00:01","rssi_dbm":-76,"rssi_valid":true,"ip":"10.0.0.2","parent_change_count":0,"parent_disconnect_count":2,"is_rootless":false,"rootless_duration_s":0,"last_parent_disconnect_reason":201,"last_wifi_disconnect_reason":201,"root_switch_count":0,"mqtt_disconnect_count":1,"mqtt_connected":true,"mqtt_disconnected_duration_s":0,"mqtt_last_connected_uptime_s":123,"mesh_rx_success_count":456,"mesh_tx_success_count":78,"mesh_tx_failure_count":0,"mesh_last_rx_success_uptime_s":122,"mesh_last_tx_success_uptime_s":123,"uptime_s":123,"free_heap_bytes":223000,"minimum_free_heap_bytes":180000,"reset_reason":"poweron","reset_reason_code":1,"boot_count":4,"last_omk_restart_reason":"none","mesh_mqtt_liveness_restart_count":0}
```

| Field | 意味 |
| --- | --- |
| `node_id` | eFuse由来の12桁Node ID |
| `mesh_layer` | ESP-WIFI-MESH layer。transition中は`-1`になり得る |
| `is_root` | 観測時点のroot状態 |
| `parent_bssid` | 観測済みparent BSSID。transition中は旧値が残り得る |
| `rssi_dbm` / `rssi_valid` | STA接続先RSSIと取得可否。取得失敗時は`rssi_valid=false` |
| `ip` | 現在または直近のSTA/internal-network IPv4。transition中は旧値が残り得る |
| `parent_change_count` | 前回と異なるparent BSSIDへの接続回数 |
| `parent_disconnect_count` | raw `MESH_EVENT_PARENT_DISCONNECTED` 回数 |
| `is_rootless` | `MESH_EVENT_NETWORK_STATE`が示す、現在のMesh networkにrootがいない状態 |
| `rootless_duration_s` | rootless状態の継続秒数。rootまたはparent接続成立時は0 |
| `last_parent_disconnect_reason` | 最後の`MESH_EVENT_PARENT_DISCONNECTED`のWi-Fi reason。未取得時は`0` |
| `last_wifi_disconnect_reason` | 最後のMesh管理STA切断reason。`MESH_EVENT_PARENT_DISCONNECTED` payloadから取得し、`201`は`WIFI_REASON_NO_AP_FOUND`。未取得時は`0` |
| `root_switch_count` | 初期root選出を除く、Node自身のroot role変化回数。同一transitionの重複eventは数えない |
| `mqtt_disconnect_count` | `MQTT_EVENT_DISCONNECTED` 回数 |
| `mqtt_connected` | status生成時点でMQTT brokerへ接続中か |
| `mqtt_disconnected_duration_s` | MQTT client開始後、現在の未接続状態が継続している秒数。接続中は0 |
| `mqtt_last_connected_uptime_s` | 最後にMQTT接続が成立した時点のNode uptime秒。一度も接続していない場合は0 |
| `mesh_rx_success_count` | Mesh internal networkで正常受信したpacketの累積数 |
| `mesh_tx_success_count` | `esp_mesh_send()`成功の累積数 |
| `mesh_tx_failure_count` | `esp_mesh_send()`失敗の累積数 |
| `mesh_last_rx_success_uptime_s` | 最後にMesh受信成功した時点のuptime秒 |
| `mesh_last_tx_success_uptime_s` | 最後にMesh送信成功した時点のuptime秒 |
| `uptime_s` | boot後秒数 |
| `free_heap_bytes` | 観測時点のfree heap |
| `minimum_free_heap_bytes` | boot後の最小free heap |
| `reset_reason` / `reset_reason_code` | ESP reset reasonの名前とコード |
| `boot_count` | NVSに保持するboot回数 |
| `last_omk_restart_reason` | OMK自身が意図的にsoftware restartした直近理由。liveness timeout時は`mesh_mqtt_liveness_timeout` |
| `mesh_mqtt_liveness_restart_count` | Mesh/MQTT liveness timeoutによるsoftware restart累積回数。NVSに保持する |

`parent_disconnect_count`と`mqtt_disconnect_count`は障害回数ではない。起動とtopology再構成の1回の事象で複数回増え得る。Dashboardや監視は単発のcounterで異常判定せず、MQTT接続状態、継続時間、計測欠測などを組み合わせて扱う。

Mesh通信が成立しているのにMQTTだけが復旧しない状態では、Nodeはsoftware restartで復旧を試みる。Mesh起動済み、parent接続済み、rootlessでない、有効IPあり、MQTT client開始済み、MQTT未接続の条件がすべて連続180秒続いたときだけrestartする。parent切断、rootless、IP未取得、MQTT接続復旧、その他の通常通信条件の喪失で、restart判定用の連続時間をリセットする。したがってMesh再構成中や通常の通信断はrestart対象にならない。restart直前には診断情報をログへ出し、`last_omk_restart_reason`と`mesh_mqtt_liveness_restart_count`で結果を確認できる。

`is_rootless`、layer、parent BSSID、IPも再構成の途中値である。特に同一SSIDで異なるBSSIDを持つGateway／市販中継機の環境では、異なるroot/treeが形成されるリスクを実機評価中である。非同期起動やNode移設後に最適rootへ自動復帰する保証はないため、root自動再選出はこの診断データを使って今後判断し、現時点では実装しない。市販中継機との併用自体は否定せず、今後の実住宅試験で評価する。

## 汎用JSONL収集

`sensor-collector`は`omk/#`をQoS 0で購読し、測定値とOMK Nodeのregistration statusを収集する。collectorはセンサ機種、`device_id`、測定項目の意味を解釈せず、受信したpayloadをトップレベルへ展開しない。

Raspberry Pi側でAsia/Tokyoの受信時刻をミリ秒付きISO 8601形式で付与し、`data/sensors/YYYY/MM/DD.jsonl`へ1メッセージ1行で追記する。通常のJSON payloadの共通構造は次のとおりである。

```json
{"received_at":"2026-07-30T10:54:12.123+09:00","topic":"omk/sen66-001/sen66","qos":0,"retain":false,"payload":{"device_id":"sen66-001"}}
```

JSONとして解析できないUTF-8 payloadは`payload_raw`と`payload_parse_error`を記録する。UTF-8でないpayloadは`payload_base64`と`payload_encoding: "base64"`で保持し、メッセージを破棄しない。CSVは一次保存形式ではなく、必要に応じてこのJSONLから後段で生成する。

## latest状態キャッシュ

JSONL保存に成功した正常JSON payloadは、payloadの`device_id`、MQTT topic、payload fieldを組み合わせた汎用latest storeにも記録される。`data/latest/items/<stable-item-id>.json`は各scalar値の最新レコード、`data/latest/catalog.json`は検出済みsource/value候補の一覧である。IDは`SHA-256(topic + NUL + device_id + NUL + field)`由来のため、BルートとBLE Plugが同じ`power` data typeを使っても衝突しない。catalogは通信断で候補を削除しない。候補から除くメタデータと保存形式は[sensor-collector README](../../services/sensor-collector/README.md)を参照する。

現行Dashboardとの互換のため、Bルート`power`（`net_power_w`を持つpayload）、SEN66`sen66`、住宅用PV・蓄電池・PCS profileの`power-flow`は、それぞれ`data/latest/broute_power.json`、`sen66.json`、`ichijo_power_flow.json`にも更新する。Plugの`power`はこの旧Bルートcacheを更新しない。collectorは同一ディレクトリの一時ファイルをatomic置換するため、Dashboardは読取り途中のJSONを参照しない。Display Item選択、推奨表示、custom表示と設定保存は現在のDashboardに実装され、設定は`data/dashboard/settings.json`へ保存される。

## BLEセンサ

SwitchBot等のBLE受信は Dashboard ではなく Raspberry Pi ホスト上の`omk-ble-sensor-manager` が行う。登録の物理 `device_key` と論理`sensor_id` を分離し、MQTT payload の `device_id` には後者だけを使用する。environment payload は `device_id`、`measured_at`、`quality`、利用可能な `temperature_c`、`relative_humidity_percent`、`co2_ppm`、`battery_percent` を含む。collector は従来通り型を解釈せず JSONL に保存する。

Gateway direct BLEとESP32 Node relayを併用するSwitchBot BLE sensorでは、Nodeは論理`sensor_id`や`device_key`を生成せず、manufacturer data、service data、BLE address、RSSIを`omk-relay/<relay_node_id>/ble/raw`へpublishする。この内部topicは`sensor-collector`と`harvest-uploader`の`omk/#`購読対象外である。BLE Sensor Managerがdirect BLEと同じdecoderとregistryでsensorを特定した後だけ、通常の`omk/<sensor_id>/{environment,motion,contact,power}`へメッセージをpublishする。direct/relayの経路選択仕様とpayload例は[BLE direct / ESP32 Node relayの経路選択](../decisions/ble-direct-relay-route-selection.md)を参照する。

BLEアドバタイズは常時受信し、runtimeのlatest値、RSSI、受信時刻はBLE受信ごとに更新する。environmentは初回の正常値を即時publishし、以後は`device_key`ごとに最短10秒間隔でpublishする。Motion Sensorは状態変化時に即時publishし、同一状態も最短10秒間隔で `{"device_id":"motion-001","measured_at":"...","motion_state":1}`の形でpublishする。`motion_state`は0=不在、1=検知である。

Contact Sensorも状態変化時に即時publishし、同一状態も最短10秒間隔で`{"device_id":"contact-001","measured_at":"...","contact_state":1}` を publishする。`contact_state`は0=閉、1=開で、公式のtimeout-not-closeも開（1）として正規化する。各rate limitはmonotonic clockを使い、`enabled=false`ではBLE observationと runtime更新を継続する一方、MQTT publishは停止する。したがってJSONLへのSwitchBot 一次保存は厳密な固定周期ではなく、通常は最短約10秒間隔となる。

Plug Miniは`{"device_id":"plug-001","measured_at":"...","power_w":173.2,"switch_state":1}`を`omk/<sensor_id>/power`へpublishする。`switch_state`は 0=OFF、1=ONで、初回は即時、同一状態は最短10秒間隔、状態変化は即時publishする。Pi実機では`omk/plug-001/power`がsensor-collectorの日次JSONLへ保存され、`power_w`と`switch_state`がpayloadのまま記録されることを確認した。BLEの受信時刻に依存するため厳密な10秒固定ではないが、通常は最短約10秒間隔で保存される。

## Bルート接続

Bルート通信はUSBシリアル、OS権限、認証、PANA通信に依存するため、当面はホスト上のsystemdサービスで実行し、コンテナ化しない。Bルート値は`omk/<device_id>/power`などへpublishされ、collectorのJSONL保存とlatest状態キャッシュへ反映される。既存の専用保存の扱いは、MQTT経由の保存を並行検証した後に判断する。

## セキュリティと運用

現在のMosquitto設定はOMK専用LAN内の匿名・平文接続である。認証、ACL、TLSは未実装である。MQTTポートをSORACOM側または他のホストインターフェースに公開してはならない。
