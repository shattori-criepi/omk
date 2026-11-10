# データ経路とMQTT仕様

MQTTは、各データ取得処理をRaspberry Piへ集約するLAN内の内部データバスである。ESP32 Nodeはnative Meshで自動選出されたRootへ送り、RootだけがOMK専用Wi-Fi経由で`192.168.50.1:1883`へ接続する。各Nodeはクラウドへ直接接続しない。Composeはbrokerを`127.0.0.1:1883`だけへ公開し、`wlan0`にbindしたsystemd socket proxyがAP clientの接続を中継する。Gateway上のhost serviceは`127.0.0.1:1883`を使用する。

現行の主経路は、センサ・Bルート・パワコン・BLEからMQTT、`sensor-collector`、JSONL、`data-transformer`、processed Parquet、Dashboardです。`harvest-uploader`は別clientとしてMQTTを直接購読し、JSONLやParquetを読まずに1分集約してSORACOM Harvestへ送信します。

## トピックとdevice_id

`device_id`は測定元を識別する論理設定値である。SEN66ではNodeの`logical_id`、BLEではregistryの`sensor_id`、Bルート・Ichijoでは各サービスの設定値を使う。物理Nodeの`node_id`やBLEの`device_key`とは区別する。測定トピックは`omk/<device_id>/<data_type>`を基本とする。OMK Nodeの登録状態は、eFuse由来の`node_id`を使う専用のregistration topicで通知する。

| 用途 | トピック | QoS | retain |
| --- | --- | --- | --- |
| Bルート瞬時電力 | `omk/<device_id>/power` | 0 | false |
| Bルート積算電力量 | `omk/<device_id>/cumulative-energy` | 0 | false |
| Bルート30分電力量 | `omk/<device_id>/interval-energy` | 0 | false |
| Ichijo電力フロー | `omk/<device_id>/power-flow` | 0 | false |
| Bルート・Ichijo可用性 | `omk/<device_id>/status` | 0 | true |
| Node登録設定 | `omk/node/<node_id>/registration/config` | 1 | true |
| Node登録ACK | `omk/node/<node_id>/registration/ack` | 1 | true |
| Node raw BLE relay | `omk-relay/<relay_node_id>/ble/raw` | 0 | false |
| Node raw SEN66 relay | `omk-relay/<node_id>/sen66/raw` | 0 | false |
| SEN66測定値 | `omk/<device_id>/sen66` | 0 | false |
| 論理device可用性 | `omk/<logical_id>/status` | 0 | true |
| OMK Node登録状態 | `omk/node/<node_id>/registration/status` | 1 | true |
| OMK Node heartbeat | `omk/node/<node_id>/status` | 0 | false |
| BLE環境センサ | `omk/<sensor_id>/environment` | 0 | false |
| BLE人感状態変化 | `omk/<sensor_id>/motion` | 0 | false |
| BLE開閉状態変化 | `omk/<sensor_id>/contact` | 0 | false |
| BLEプラグ電力 | `omk/<sensor_id>/power` | 0 | false |

表のQoSはpublisher側の設定である。Bルート・Ichijoは従来どおり各サービスがpublishする。Nodeのregistration status/ack・heartbeat・raw relayはRootが代理publishする。SEN66もRootから物理Node IDの内部raw topicへ送り、BLE Sensor Managerが台帳の現所有者・適用revision・希望revisionを照合してから既存のSEN66測定topicへpayloadを変更せずpublishする。これにより旧Nodeの滞留データをCollector・Dashboard・Harvestへの配信前に除外する。BLE Sensor Managerはraw BLEをdecodeして通常のBLE測定値へ変換し、Nodeへのregistration configと論理device可用性もpublishする。Rootは全NodeのconfigをQoS 1で購読し、対象NodeへMeshで転送する。

collectorとHarvestは`omk/#`をQoS 0で購読する。collectorは管理メッセージもJSONLへ保存するが、transformerは管理topicをParquetから除外し、Harvestも測定対象以外を集約しない。USB provisioningの完了確認も対象Nodeのregistration statusを使用する。

SEN66測定トピックの例は`omk/sen66-001/sen66`である。

論理deviceの`omk/<logical_id>/status`はGatewayがretain付きでpublishする。Nodeの30秒heartbeatまたは新しいSEN66/BLE転送を受信するとonline、120秒受信がなければofflineとなる（判定周期5秒）。retained登録状態はonlineの根拠にしない。Node別のMQTT session/LWTは廃止する。

## Payload

### Bルート・Ichijo

Bルートpayloadはすべて`device_id`を持ち、次のfieldを送る。

| data type | 時刻field | 測定field・品質 |
| --- | --- | --- |
| `power` | `measured_at` | `net_power_w`（買電が正、売電が負） |
| `cumulative-energy` | `metered_at`、`received_at` | `cumulative_energy_import_kwh`、`cumulative_energy_export_kwh` |
| `interval-energy` | `start_at`、`end_at` | `import_energy_kwh`、`export_energy_kwh`、`quality_status` |

時刻はoffset付きISO 8601。積算payloadの`received_at`はBルート取得側の時刻で、collectorの外側の`received_at`とは別である。30分値は正常時`quality_status=normal`、逆方向非対応時は`reverse_not_supported`で売電量が`null`となる。前回値なし・非30分間隔・負の積算差分では30分値自体を生成・publishしない。瞬時・積算payloadには`quality`や`source`を付けない。

Ichijoの`power-flow`は取得サイクル終了時の`measured_at`、PV・系統・蓄電池・PCS・負荷の値、`quality`（`normal`/`degraded`）、`errors`を持つ。取得失敗は`null`で保持する。全fieldとJSON例は[Ichijo README](../../services/ichijo-energy-node/README.md#mqtt)を参照する。Bルート・Ichijoの可用性payloadは`device_id`と`status`（`online`/`offline`）を持ち、Ichijoだけが`timestamp`も付ける。statusは接続時・正常終了時・Last Will用で、計測時刻や品質の代わりにはならない。

### SEN66・Node登録

測定値はSEN66の取得成功時に送信する。未取得値は非標準の`NaN`ではなくJSONの`null`にする。ESP32は時刻同期をしていないため`measured_at`を含めず、sensor-collectorが付与するGateway受信時刻`received_at`を保存・集計に使用する。

```json
{"device_id":"sen66-001","uptime_ms":123456,"pm1_0_ug_m3":4.1,"pm2_5_ug_m3":6.8,"pm4_0_ug_m3":8.2,"pm10_0_ug_m3":10.5,"relative_humidity_percent":48.2,"temperature_celsius":25.3,"voc_index":92.0,"nox_index":null,"co2_ppm":612.0}
```

`harvest-uploader`はSEN66のtopic中のLogical IDをHarvest field prefixに使う。たとえば`omk/sen66-001/sen66`は`sen66-001_temperature_c`と`sen66-001_co2_ppm`を生成し、`omk/sen66-002/sen66`は別の`sen66-002_temperature_c`と`sen66-002_co2_ppm`を生成する。複数SEN66の1分平均は相互に混合しない。

Nodeの測定payloadには、従来の値に加えて`protocol_version:2`、`node_id`、`revision`、16桁hexの`boot_id`、種別ごとの`seq`を付ける。SEN66の`meta.boot_id`/`meta.seq`/`meta.enqueue_seq`も維持する。Gatewayの可用性管理は重複・既知の旧boot・逆順の受信で鮮度を延長しない。collectorのJSONL保存にexactly-onceや全経路の並べ替えを追加するものではない。

Nodeは30秒ごとと登録変更時に、登録状態をRootへ送る。Rootは`registration/status`をQoS 1・retain付きでpublishする。

```json
{"protocol_version":2,"node_id":"112233445566","revision":7,"registration_state":"registered","logical_id":"sen66-001","capabilities":3,"connected_sensors":["sen66"],"boot_id":"0123456789abcdef","seq":12,"uptime_s":360}
```

新しいGatewayはこの状態から適用済みIDを取り込める。Gatewayに保存済みの解除・変更要求がある場合は希望状態を優先して再同期し、古いretained状態で登録を復活させない。IDは1〜48文字のASCII英数字・`-`・`_`、他Nodeとの重複は拒否する。

登録設定は`{"protocol_version":2,"revision":8,"logical_id":"sen66-002"}`、解除は同じ形式で`logical_id:null`。Gatewayは希望状態を永続化してからretained QoS 1で送り、未完了設定を5秒周期で再送する。NodeはNVSへ適用した実際のrevisionとIDを`registration/ack`で返し、`result`は`applied`または`rejected`。ACK消失時は次回状態通知からも適用完了を確認できる。変更なしの再接続でACKは作らない。適用済み解除のretained config/ackは消去するが、GatewayとNodeの解除revisionは保持する。

解除・変更の適用確認までは旧IDと途中の未確認要求IDを予約し、別Nodeへの割当を拒否する。設定再送はBroker接続中だけ行い、Paho標準キューを64件に制限する。送信中の要求と5秒以内の同revision要求は重複enqueueしない。再接続時は保存済みの最新希望状態から再送する。

登録MQTT v1との恒久的な互換経路はない。Gatewayと全Nodeの移行が必要。USB provisioning v2とDiscovery BLE v1は引き続き利用する。

### Node heartbeat

`omk/node/<node_id>/status`は30秒周期・QoS 0・非retain。上記の登録状態と共通の識別field・`uptime_s`を持つ。旧版のMesh/MQTT接続診断fieldは出力しない。SEN66の`sen66_rc`/`sen66_to`はsensor復旧情報として維持する。旧netif/IP/restart budget/詳細ネットワーク診断のfieldとtopicは廃止する。

通信queueは有限で、期限切れやRoot交代時にはデータを破棄する。SDK内の滞留を含む端到端の期限や無欠測は保証しない。責務と限界は[Mesh設計](../decisions/esp-wifi-mesh-node-networking.md)を参照。

## 汎用JSONL収集

`sensor-collector`は`omk/#`をQoS 0で購読し、測定値とOMK Nodeのregistration statusを収集する。JSONLのpayloadを機種別の列へ展開しない。ただし保存前に、通常BLE topicの`device_id`と`source`に基づくdirect優先フィルタを適用する。direct受信から30秒未満のrelay観測はJSONL・latestともに更新しない。sourceがない旧payloadは従来どおり保存する。

Raspberry Pi側でAsia/Tokyoの受信時刻をミリ秒付きISO 8601形式で付与し、`data/sensors/YYYY/MM/DD.jsonl`へ1メッセージ1行で追記する。通常のJSON payloadの共通構造は次のとおりである。

```json
{"received_at":"2026-07-30T10:54:12.123+09:00","topic":"omk/sen66-001/sen66","qos":0,"retain":false,"payload":{"device_id":"sen66-001"}}
```

JSONとして解析できないUTF-8 payloadは`payload_raw`と`payload_parse_error`を記録する。UTF-8でないpayloadは`payload_base64`と`payload_encoding: "base64"`で保持し、メッセージを破棄しない。汎用経路のCSVはParquetから後段で生成する。Bルートサービスは別途、専用CSVをMQTT配送前にも保存する。

## latest状態キャッシュ

JSONL保存に成功した正常JSON payloadは、payloadの`device_id`、MQTT topic、payload fieldを組み合わせた汎用latest storeにも記録される。`data/latest/items/<stable-item-id>.json`は各scalar値の最新レコード、`data/latest/catalog.json`は検出済みsource/value候補の一覧である。IDは`SHA-256(topic + NUL + device_id + NUL + field)`由来のため、BルートとBLE Plugが同じ`power` data typeを使っても衝突しない。catalogは通信断で候補を削除しない。候補から除くメタデータと保存形式は[sensor-collector README](../../services/sensor-collector/README.md)を参照する。

現行Dashboardとの互換のため、Bルート`power`（`net_power_w`を持つpayload）、SEN66`sen66`、住宅用PV・蓄電池・PCS profileの`power-flow`は、それぞれ`data/latest/broute_power.json`、`sen66.json`、`ichijo_power_flow.json`にも更新する。Plugの`power`はこの旧Bルートcacheを更新しない。collectorは同一ディレクトリの一時ファイルをatomic置換するため、Dashboardは読取り途中のJSONを参照しない。Display Item選択、推奨表示、custom表示と設定保存は現在のDashboardに実装され、設定は`data/dashboard/settings.json`へ保存される。

## BLEセンサ

SwitchBot等のBLE受信は Dashboard ではなく Raspberry Pi ホスト上の`omk-ble-sensor-manager` が行う。登録の物理 `device_key` と論理`sensor_id` を分離し、MQTT payload の `device_id` には後者だけを使用する。environment payload は `device_id`、`measured_at`、`quality`、利用可能な `temperature_c`、`relative_humidity_percent`、`co2_ppm`、`battery_percent` を含む。collector は従来通り型を解釈せず JSONL に保存する。

Gateway direct BLEとESP32 Node relayを併用するSwitchBot BLE sensorでは、Nodeは論理`sensor_id`や`device_key`を生成せず、manufacturer data、service data、BLE address、RSSIを`omk-relay/<relay_node_id>/ble/raw`へpublishする。この内部topicは`sensor-collector`と`harvest-uploader`の`omk/#`購読対象外である。BLE Sensor Managerがdirect BLEと同じdecoderとregistryでsensorを特定した後だけ、通常の`omk/<sensor_id>/{environment,motion,contact,power}`へメッセージをpublishする。direct/relayの経路選択仕様とpayload例は[BLE direct / ESP32 Node relayの経路選択](../decisions/ble-direct-relay-route-selection.md)を参照する。

BLE測定値には`source: "direct"`または`"relay"`を付け、relayの場合だけ`relay_node_id`を加える。`measured_at`はGatewayでのBLE callback/raw relay受信時刻で、センサ本体の計測時計ではない。`quality: "normal"`はenvironmentとpowerに付け、motion/contactには付けない。下記のJSONは主要測定fieldの抜粋であり、batteryやlight等もdecoderが取得できた場合は含む。

BLEアドバタイズは常時受信し、登録済み一覧のruntime latest値、RSSI、受信時刻はdirect受信で更新する。この一覧の受信状態とrelay由来のMQTT測定値は別である。environmentは初回の正常値を即時publishし、以後は`device_key`ごとに最短10秒間隔でpublishする。Motion Sensorは状態変化時に即時publishし、同一状態も最短10秒間隔で `{"device_id":"motion-001","measured_at":"...","motion_state":1}`の形でpublishする。`motion_state`は0=不在、1=検知である。

Contact Sensorも状態変化時に即時publishし、同一状態も最短10秒間隔で`{"device_id":"contact-001","measured_at":"...","contact_state":1}` を publishする。`contact_state`は0=閉、1=開で、公式のtimeout-not-closeも開（1）として正規化する。direct/relay経路の切替時は同値でも最初の観測を即時publishする。各rate limitはmonotonic clockを使い、`enabled=false`ではBLE observationと runtime更新を継続する一方、MQTT publishは停止する。したがってJSONLへのSwitchBot 一次保存は厳密な固定周期ではなく、通常は最短約10秒間隔となる。

Plug Miniは`{"device_id":"plug-001","measured_at":"...","power_w":173.2,"switch_state":1}`を`omk/<sensor_id>/power`へpublishする。`switch_state`は 0=OFF、1=ONで、初回は即時、同一状態は最短10秒間隔、状態変化は即時publishする。Pi実機では`omk/plug-001/power`がsensor-collectorの日次JSONLへ保存され、`power_w`と`switch_state`がpayloadのまま記録されることを確認した。BLEの受信時刻に依存するため厳密な10秒固定ではないが、通常は最短約10秒間隔で保存される。

## Bルート接続

Bルート通信はUSBシリアル、OS権限、認証、PANA通信に依存するため、当面はホスト上のsystemdサービスで実行し、コンテナ化しない。Bルート値は`omk/<device_id>/power`などへpublishされ、collectorのJSONL保存とlatest状態キャッシュへ反映される。現在は専用CSVへ保存した後、MQTTへbest-effortで送信する。MQTT切断中の値はMQTT再送用に保持しないため、CSVとcollectorのJSONLの件数が常に一致するとは限らない。

## セキュリティと運用

現在のMosquitto設定はOMK専用LAN内の匿名・平文接続である。認証、ACL、TLSは未実装である。MQTTポートをSORACOM側または他のホストインターフェースに公開してはならない。
