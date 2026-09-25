# BLE direct / ESP32 Node relayの経路選択

- **決定日:** 2026-08-17
- **ステータス:** 採用
- **対象:** SwitchBot BLE sensorをGateway direct BLEとESP32 Node relayの両方で受信する経路

## 背景

OMKでは、BLEセンサを次の二つの経路で受信できる。

1. Gateway Raspberry PiがBlueZで直接BLEアドバタイズを受信する経路
2. ESP32 NodeがBLEアドバタイズを受信し、Wi-Fi/MQTTでGatewayへrelayする経路

これはBLE到達範囲の拡大と通信冗長化に有効である。一方、同じ物理センサを両方が受信すると、同じ測定値がJSONL一次保存とHarvestの1分集約へ二重に入る。特にHarvestでは、二つの経路をそのまま平均に加えると値の重み付けが変わり、データの信頼性を損なう。

## 決定

BLEアドバタイズ単位で「同じBLE受信データか」を厳密に照合しない。OMKでは10秒値をBLE受信ごとに完全に保存することより、最終的な1分集約値を安定させることを優先する。そのため、測定値の同一性ではなく、deviceごとに現在採用する受信経路を選択する。

- Gateway direct BLEを主系とする。
- ESP32 Node relayをfallbackとする。
- 有効なdirect observationを受信したら、通常のsensor topicへのpublishのrate limitや値変化と独立してdeviceごとの`last_direct_seen`を更新する。
- direct受信から30秒**未満**のrelayは破棄する。
- direct受信から30秒**以上**経過したrelayは採用する。
- directが復帰したら、その最初のdirectから即時に主系へ戻す。
- freshness判定はwall clockではなくmonotonic時刻で行う。
- 状態はdeviceごとに独立させる。

SwitchBot等は通常およそ10秒周期で受信できるため、30秒は1〜2回程度BLEアドバタイズを受信できなくてもrelayへ切り替えず、約3周期のdirect未受信でfallbackとする値である。30秒ちょうどはrelayを採用する境界とする。

## Gatewayでのsensor特定とrelay入力

ESP32 NodeはGatewayの論理`sensor_id`を持たない。NodeはSwitchBotの機種判定、decode、`device_key`生成を行わず、manufacturer dataとfd3d service data、BLE address、RSSIをraw observationとしてrelayする。Gatewayがdirect BLEと同じ`switchbot.decode()`でphysical identityと`device_key`を決める。

```text
switchbot:<12桁lowercase hex>
```

例:

```text
switchbot:020000000001
```

NodeからGatewayへの入力は、通常のsensor topicではなく次の内部topicである。

```text
omk-relay/<relay_node_id>/ble/raw
```

```json
{
  "protocol_version": 1,
  "relay_node_id": "112233445566",
  "ble_address": "020000000001",
  "rssi": -45,
  "manufacturer_data": [{"company_id": 2409, "data": "..."}],
  "service_data": [{"uuid": "0000fd3d-0000-1000-8000-00805f9b34fb", "data": "..."}]
}
```

`omk-relay/...`は`sensor-collector`と`harvest-uploader`が購読する`omk/#`の外に置く。したがって、Gatewayでsensorを特定する前のrelay入力はJSONL保存・Harvest集約へ入らない。

GatewayのBLE Sensor Managerは既存registryで`device_key`に対応するsensorを特定する。たとえば、登録済みの`switchbot:020000000001`を`th-001`へ対応付け、enabledなenvironment sensorだけを次の通常のsensor topicへ再publishする。`measured_at`にはNodeの時計ではなくGateway受信時刻を付与する。

```text
omk/th-001/environment
```

```json
{
  "device_id": "th-001",
  "measured_at": "...",
  "quality": "normal",
  "temperature_c": 24.4,
  "relative_humidity_percent": 48,
  "source": "relay",
  "relay_node_id": "112233445566"
}
```

未登録またはdisabledの`device_key`は通常のsensor topicへpublishしない。自動登録もしない。direct側も同じ`omk/th-001/environment`へ`source: "direct"`を付けてpublishするため、後段は経路に関係なく同じ`device_id`単位で判定できる。

Node自身の`logical_id`（例: `sen66-001`）と、NodeがrelayするBLE sensorの`device_key`およびGatewayの`sensor_id`は別概念である。Nodeのlogical IDをBLE sensor IDへ流用しない。

## selectorの適用位置

BLE Sensor Managerは、登録済みsensorの有効なdirect observationを受信するたびにfreshnessを更新し、directが新しい間はrelayを通常topicへpublishしない。direct復帰時は通常のrate limitにかかわらず最初のdirectをpublishする。

MQTT broker上でdirectとrelayが同じ`omk/<sensor_id>/{environment,motion,contact,power}`へ届いた場合にも備え、独立したsinkでも同じ30秒境界の選択を行う。

- `sensor-collector`: JSONL append直前
- `harvest-uploader`: `MinuteAggregator.ingest()`直前

両サービスは独立したMQTT clientとして`omk/#`を購読し、各clientが受信したdirectメッセージの時刻を基準に選択する。BLE Sensor Managerが持つBLE受信ごとの時刻は共有しない。`source`がない旧payloadは移行互換のため従来どおり通過させる。SEN66、Bルート、住宅用PV・蓄電池・PCS profileの`power-flow`などBLE sensorの通常topic以外は対象外である。

## 採用時の実機E2E確認

以下は採用時の試験記録である。現在は上記のとおりBLE Sensor Managerでもrelayを抑止するため、direct正常時に両sourceがbrokerへ流れることは通常動作の要件ではない。

- **direct + relay同時動作:** broker上で`omk/th-001/environment`の`source=direct`と`source=relay`を確認した。collector JSONLにはdirectだけが保存され、relayは抑止された。
- **direct停止:** Gateway Bluetoothを`rfkill`でsoft blockし、BLE Sensor Managerを動作させたままdirectを途絶させた。30秒以上経過後、collector JSONLへ`source=relay`が保存された。
- **direct復帰:** Gateway Bluetoothを復帰させてBLE scanを再開すると、最初のdirectから即時に`source=direct`へ戻り、以降のrelayは再びJSONLから抑止された。

したがって、`direct正常 → direct断 → relay fallback → direct復帰`のE2E動作を確認済みである。

Presence Sensor Proでは、Node relayがmanufacturer dataとfd3d service dataを結合して送信できることを確認した。Gateway内蔵Bluetoothでは同機器のScan Responseを取得できない実機差があるため、Node relayとは別のdirect BLE側制約として扱う。SEN66とraw relayの同時動作では、不要なraw publishを発生源で抑えることで、SEN66 telemetry、Mesh status、MQTTが継続することを確認した。

## 制約と今後

採用時の試験では、Gateway Bluetoothを`rfkill block bluetooth`後に`rfkill unblock bluetooth`してもBlueZ scanは自動復帰せず、BLE Sensor Managerの再起動が必要だった。これはroute selectorとは別の復旧上の制約である。

Node relayには次の現時点の制約がある。

- Meter / Meter Plus、Meter Pro CO2、防水温湿度計、人感、Presence Sensor Pro、開閉、Plug MiniをGateway decoderと同じ経路で扱う。
- raw relayはdeviceごとに原則最短10秒に制限する。active scanでADVとScan Responseが結合してmanufacturer dataまたはservice dataが追加される場合だけ、10秒内に1回の完全化更新を許可する。
- rate-limit状態はメモリ上だけに保持する。
- 同時追跡は固定16台までである。

長期運用では、SEN66の欠測、relay経由の異常、`parent_disconnect_count`、`mqtt_disconnect_count`、Mesh parent変更、root交代、意図しない再起動、heapの継続的低下を確認する。
