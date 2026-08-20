# OMK BLEセンサマネージャー

このRaspberry Piホストサービスは、BlueZのBLE広告を受動的に受信し、物理的な
`device_key`をOMKの論理的な`sensor_id`へ対応付け、
`omk/living-env-01/environment`のようなベンダー非依存のMQTT topicをpublishします。

このサービスはDashboardコンテナの外で動作し、Dockerはホストゲートウェイ経由で
ポート8787へ接続します。BlueZのDBusソケットや特権コンテナは不要なため、Bluetoothへの
アクセスはホストサービスに分離されたままです。DashboardをローカルUI以外へ公開する場合は、
Piのファイアウォールでポート8787をDockerブリッジに限定してください。

Pi上で`scripts/setup-ble-sensor-manager.sh`を実行すると、virtualenvの作成とsystemd unitの
導入・再起動を行えます。unitは`OMK_BLE_REGISTRY`と`OMK_NODE_REGISTRY`をそれぞれ
`data/ble/sensors.json`と`data/ble/nodes.json`へ設定し、`data/ble`だけを書込み可能にした
まま`ProtectSystem=strict`を維持します。登録情報はこれらの可読なJSONへ保存します。

## ESP32 Node初回Wi-Fi設定

Nodeの初回Wi-Fi設定はBLE Sensor Managerでは行わない。NodeをGatewayへUSB接続し、
`python3 scripts/provision_omk_node_via_usb.py`を実行する。USB transportはNode IDを取得し、
credential保存・software reboot・MQTT registration statusまでを確認する。通常のBLE scan、
SwitchBot relay、MQTT Node registrationはこの操作から独立して継続する。

`device_key`は物理的な識別子（`switchbot:<コロンを除いた小文字MAC>`）、`sensor_id`は
OMKの論理的な識別子です。registryには`device_key`、`sensor_id`、`sensor_type`、vendor、
model、location、`display_name`、`enabled`だけを保存します。最新の測定値、RSSI、受信時刻、
生のBLE広告はruntime stateとして保持し、registryへは書き込みません。

## ESP32 Node relay入力

ESP32 NodeはGatewayの`sensor_id`を持たず、SwitchBot Meter形式のBLE advertisementから得た
`switchbot:<12桁lowercase hex>`の`device_key`を次の内部MQTT topicへpublishします。

```text
omk-relay/<relay_node_id>/ble/environment
```

このサービスはこのtopicを購読し、既存registryで`device_key`を解決します。登録済みかつ
`enabled=true`のenvironment sensorだけを、Gateway受信時刻を`measured_at`として
`omk/<sensor_id>/environment`へ`source: "relay"`と`relay_node_id`を保ったまま再publishします。
未登録・disabledのdevice_keyは通常sensor topicへpublishせず、自動登録もしません。

direct BLEも同じcanonical topicへ`source: "direct"`でpublishします。経路の冗長化、30秒の
direct優先fallback、JSONL/Harvestでの重複排除は
[`docs/decisions/ble-direct-relay-route-selection.md`](../../docs/decisions/ble-direct-relay-route-selection.md)を参照してください。

Dashboardのセンサ管理セットアップフローでは、未登録のBLE広告を発見・分類し、IDを提案した後、
選択した表示名とlocationで登録します。`temperature_humidity_sensor`と`waterproof_sensor`には
未使用で最小の`th-xxx`、`co2_sensor`には`co2-xxx`、状態センサには対応する
`motion-xxx` / `contact-xxx`を提案します。センサを交換した場合は、原則として新しい論理IDを
発行します。

未知のSwitchBot BLE広告は、セットアップ中にmanufacturer dataとservice dataの生hexを付けて
意図的に表示します。推測した値をpublishせずに、Meter Pro CO2、人感センサ、開閉センサを
実機で検証できるようにするためです。

OMKでは製品名と内部modelを分離しています。SwitchBot MeterおよびMeter Plusは
`temperature_humidity_sensor`、SwitchBot Meter Pro CO2は`co2_sensor`を使用します。旧来の
`meter`、`meter_plus`、`meter_pro_co2`を持つ既存registryは互換的に読み込み、次回のregistry更新時に
現行名へ書き換えます。

| OMK model | Dashboard表示名 | sensor type | 正規化後の値 |
| --- | --- | --- | --- |
| `temperature_humidity_sensor` | SwitchBot 温湿度計 | `environment` | `temperature_c`, `relative_humidity_percent` |
| `co2_sensor` | SwitchBot CO2センサー | `environment` | 温度、湿度、`co2_ppm` |
| `motion_sensor` | SwitchBot 人感センサー | `motion` | `motion_state` (0=不在, 1=検知) |
| `contact_sensor` | SwitchBot 開閉センサー | `contact` | `contact_state` (0=閉, 1=開) |
| `waterproof_sensor` | SwitchBot 防水温湿度計 | `environment` | `temperature_c`, `relative_humidity_percent` |
| `plug_sensor` | SwitchBot プラグミニ | `power` | `power_w`, `switch_state` (0=OFF, 1=ON) |

`waterproof_sensor`は屋外専用ではありません。設置用途は`location`（例: 屋外、浴室）で
表します。`humidity_percent`、`outdoor_meter`、`waterproof_meter`はOMKのschema/model名では
ありません。

## 登録の削除と交換

管理画面の`/admin/sensors`では、編集画面から登録済みBLEセンサを`device_key`単位で
登録解除できます。この操作はregistryの登録情報だけを削除し、JSONL、Parquet、Harvest retry
queue、SORACOM Harvest Data、MQTTの過去履歴には一切触れません。削除された`sensor_id`は空くため、
故障交換時は旧センサを削除し、セットアップモードで発見した新品へ同じ`sensor_id`を再使用できます。

`enabled=false`は一時的な停止であり、registryと`sensor_id`の占有を維持しつつruntime stateの更新だけを
続け、MQTT publishを止めます。一方、削除後はregistryから完全に外れ、以後MQTT publishしません。周囲で
同じ物理デバイスのBLE広告が続いていれば、未登録candidateとして再び表示され、セットアップから再登録できます。

BLE広告はペアリングなしで継続受信します。`enabled=true`のenvironmentセンサは、最初に有効な
値を受信した時点で即時publishし、その後は`device_key`ごとに最短10秒間隔でpublishします。
runtime stateは広告を受信するたびに更新します。motionとcontactは状態変化時に即時publishし、
加えて現在状態を最短10秒間隔でpublishします。これらの制限にはmonotonic clockを使用します。
Plug Miniの電力も同じ10秒制限に従いますが、`switch_state`の変化時は即時publishします。
`enabled=false`のセンサもruntime stateは更新しますが、MQTTはpublishしません。Plug Miniには
`plug-xxx`を提案し、`power_w`と`switch_state`を含む`omk/<sensor_id>/power`をpublishします。

現在Piで取得したmanufacturer layoutでは、SwitchBot Meterの温度・湿度と、Meter Pro CO2の温度・
湿度・CO2をdecodeできます。CO2 layoutは2台の実機で測定値が一致したことに基づき、個体ごとの
MACアドレスを使用せず、可変byteを定数とはみなしません。

将来のclassifier変更では、[2026-08-20 SwitchBot raw capture監査](docs/switchbot-raw-capture-audit-2026-08-20.md)
に記録した個体数・capture数・byteごとの根拠レベルも参照してください。

## Raspberry Piでの確認事項

Piの現在のBlueZ/bleakの組み合わせでは、SwitchBot BLE広告が`manufacturer_data[0x0969]`だけで
報告されることがあります。そのため、`service_data`が空であってもエラーではありません。
新しいfixtureを取得する際、`raw_scan`は両方の構造を出力します。

```sh
PYTHONPATH=src .venv/bin/python -m omk_ble.raw_scan --seconds 30
```

サービスを起動する前に、`rfkill list bluetooth`と`bluetoothctl show`でアダプタを確認します。
soft blockされている場合は、`sudo rfkill unblock bluetooth`、続いて
`sudo bluetoothctl power on`を実行してください。セットアップスクリプトも可能な範囲でこれらを
実行します。アダプタがない、または電源を投入できない場合は、導入を失敗させず警告を記録します。

Raspberry Piで実機確認済み:

- SwitchBot 温湿度計: BLE広告の受信、温度・湿度のdecode。
- SwitchBot CO2センサー: BLE広告の受信、温度・湿度・CO2 ppmのdecode（2台の実機で確認）。
- SwitchBot 人感センサー: BLE広告の受信、状態遷移、定期MQTT publish。
- SwitchBot 開閉センサー: BLE広告の受信、状態遷移、定期MQTT publish。
- SwitchBot 防水温湿度計: BLE広告の受信、専用temperature/humidity manufacturer layoutのdecode。
- SwitchBot プラグミニ: BLE広告のみからの電力・switch stateのdecode。manufacturer layoutを主な
  識別根拠とし、公開されている`0x67`および国内実機で観測した`0x6a`のservice data値は補助的にのみ
  用います。電力値はMSBのoverload flagをmaskしてから、0.1 W scaleを適用します。

### SwitchBot プラグミニ実機確認

Raspberry Piと実Plug Miniで、ペアリングなしのBLE advertisement受信からDashboardの
BLEセットアップ、`plug-001`登録、MQTT、JSONL保存、Harvest 1分集約、SORACOM
Harvest Data送信までを確認した。内部modelは`plug_sensor`、sensor typeは`power`、
Dashboard表示名は「SwitchBot プラグミニ」である。

manufacturer dataではstate byteのbit 7を`switch_state`として用い、0=OFF、1=ONを
実機のOFF→ON操作と照合した。電力は公式仕様に従い
`raw_power = ((byte10 & 0x7f) << 8) | byte11`、`power_w = raw_power / 10.0`
でdecodeする。byte10のbit 7はoverload flagであり電力値から除外する。低負荷で
`0x0034`=5.2 W、`0x0035`=5.3 W（アプリ表示約5.1〜5.2 W）、高負荷で
`0x05f2`=152.2 W、`0x06c4`=173.2 W、`0x06cd`=174.1 W（実負荷約170 W）を確認し、
低負荷・高負荷とも0.1 W scaleが成立した。

国内実機ではfd3d Service Dataとして`6a0064`を観測したが、これは公式device typeと
は記載しない。Service Dataは補助識別に留め、Plug Mini判定とmeasurement decodeは
manufacturer dataの12-byte layoutを主根拠にする。

国内Plug Miniの複数個体で12-byte manufacturer advertisementを確認している。byte 8は旧個体で
`0x16`、新品個体で`0x10`を観測したため、固定classifierには使用しない。service dataで公開値
`0x67`または国内実測値`0x6a`を補助識別できる場合、state byte（index 7）と末尾2 byteから値を
decodeする。state byteのbit 7は`switch_state`、電力は
`((data[10] & 0x7f) << 8) | data[11]`に0.1 W scaleを適用する。新品実機の
`...8010370037`はON / 5.5 Wとして確認した。service dataがないmanufacturer-onlyの判定では、
誤認防止のため従来の`0x16` markerをstrict fallbackとして維持する。`0x10`および`0x16`の意味は
現時点で断定しない。

Piで行指向のraw captureを取得するには、
`PYTHONPATH=src .venv/bin/python -m omk_ble.raw_scan --seconds 30`を使用します。
