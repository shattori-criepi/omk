# BLE direct / ESP32 Node relayの経路選択

- **決定日:** 2026-08-17
- **ステータス:** 採用
- **対象:** SwitchBot等のBLE environment sensorをGateway direct BLEとESP32 Node relayの両方で受信する経路

## 背景

OMKでは、BLEセンサを次の二つの経路で受信できる。

1. Gateway Raspberry PiがBlueZで直接BLE広告を受信する経路
2. ESP32 NodeがBLE広告を受信し、Wi-Fi/MQTTでGatewayへrelayする経路

これはBLE到達範囲の拡大と通信冗長化に有効である。一方、同じ物理センサを両方が受信すると、同じ測定値がJSONL一次保存とHarvestの1分集約へ二重に入る。特にHarvestでは、二つの経路をそのまま平均に加えると値の重み付けが変わり、データの信頼性を損なう。

## 決定

BLE advertisement単位で「同じ広告か」を厳密に照合しない。OMKでは10秒値を広告単位で完全に保存することより、最終的な1分集約値を安定させることを優先する。そのため、測定値の同一性ではなく、deviceごとに現在採用する受信経路を選択する。

- Gateway direct BLEを主系とする。
- ESP32 Node relayをfallbackとする。
- directを受信したら常に採用し、deviceごとの`last_direct_seen`を更新する。
- direct受信から30秒**未満**のrelayは破棄する。
- direct受信から30秒**以上**経過したrelayは採用する。
- directが復帰したら、その最初のdirectから即時に主系へ戻す。
- freshness判定はwall clockではなくmonotonic時刻で行う。
- 状態はdeviceごとに独立させる。

SwitchBot等は通常およそ10秒周期で受信できるため、30秒は1〜2回の広告欠落だけではrelayへ切り替えず、約3周期のdirect欠落でfallbackとする値である。30秒ちょうどはrelayを採用する境界とする。

## canonical device identityとrelay入力

ESP32 NodeはGatewayの論理`sensor_id`（例: `th-001`）を持たない。Nodeは対応するSwitchBot Meter形式のmanufacturer dataから物理識別子を取り出し、Gateway registryと同じ次の形式の`device_key`としてrelayする。

```text
switchbot:<12桁lowercase hex>
```

例:

```text
switchbot:cf3941c7ed79
```

NodeからGatewayへの入力は、通常のsensor topicではなく次の内部topicである。

```text
omk-relay/<relay_node_id>/ble/environment
```

```json
{
  "device_key": "switchbot:cf3941c7ed79",
  "quality": "normal",
  "temperature_c": 24.4,
  "relative_humidity_percent": 48,
  "source": "relay",
  "relay_node_id": "09dda0d5a8f2"
}
```

`omk-relay/...`は`sensor-collector`と`harvest-uploader`が購読する`omk/#`の外に置く。したがって、canonical化前のrelay入力はJSONL保存・Harvest集約へ入らない。

GatewayのBLE Sensor Managerは既存registryで`device_key`を解決する。たとえば、登録済みの`switchbot:cf3941c7ed79`を`th-001`へ解決し、enabledなenvironment sensorだけを次のcanonical topicへ再publishする。`measured_at`にはNodeの時計ではなくGateway受信時刻を付与する。

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
  "relay_node_id": "09dda0d5a8f2"
}
```

未登録またはdisabledの`device_key`はcanonical sensor topicへpublishしない。自動登録もしない。direct側も同じ`omk/th-001/environment`へ`source: "direct"`を付けてpublishするため、後段は経路に関係なく同じ`device_id`単位で判定できる。

Node自身の`logical_id`（例: `sen66-001`）と、NodeがrelayするBLE sensorの`device_key`およびGatewayの`sensor_id`は別概念である。Nodeのlogical IDをBLE sensor IDへ流用しない。

## selectorの適用位置

MQTT broker上では、directとGatewayでcanonical化済みのrelayが同じ`omk/<sensor_id>/environment`へ流れてよい。重複排除は独立したsinkごとに実施する。

- `sensor-collector`: JSONL append直前
- `harvest-uploader`: `MinuteAggregator.ingest()`直前

両サービスは独立したMQTT clientとして`omk/#`を購読するため、同一のroute selection仕様をそれぞれ持つ。`source`がない旧payloadは移行互換のため従来どおり通過させる。SEN66、Bルート、一条パワコンなどenvironment BLE以外のtopicは対象外である。

## 実機E2E確認（2026-08-17）

対象はGateway Raspberry Pi、AtomS3 Lite Node ID `09dda0d5a8f2`、SwitchBot温湿度計`switchbot:cf3941c7ed79`（Gateway sensor ID `th-001`）である。

- **direct + relay同時動作:** broker上で`omk/th-001/environment`の`source=direct`と`source=relay`を確認した。collector JSONLにはdirectだけが保存され、relayは抑止された。
- **direct停止:** Gateway Bluetoothを`rfkill`でsoft blockし、BLE Sensor Managerを動作させたままdirectを途絶させた。30秒以上経過後、collector JSONLへ`source=relay`が保存された。
- **direct復帰:** Gateway Bluetoothを復帰させてBLE scanを再開すると、最初のdirectから即時に`source=direct`へ戻り、以降のrelayは再びJSONLから抑止された。

したがって、`direct正常 → direct断 → relay fallback → direct復帰`のE2E動作を確認済みである。

## 制約と今後

Gateway Bluetoothを`rfkill block bluetooth`後に`rfkill unblock bluetooth`しても、BLE Sensor ManagerのBlueZ scanは自動復帰しなかった。BLE Sensor Managerを再起動すると復帰した。これはroute selectorとは別問題であり、現時点でコード変更は行わない。将来の耐障害性改善候補として扱う。

Node relayには次の現時点の制約がある。

- 対応するSwitchBot Meter形式だけを扱う。
- publish間隔はdeviceごとに最短10秒である。
- rate-limit状態はメモリ上だけに保持する。
- 同時追跡は固定8台までである。

現段階ではこの範囲で十分とし、動的な管理frameworkは導入しない。2026-08-18以降は、direct/relay切替の頻度、relayによる二重集約の有無、SEN66とBLE relayを併用するNodeの長時間安定性、再起動・crash・継続的欠測、必要に応じたESP32 heap推移を実運用ログで確認する。現時点では追加実装を行わない。
