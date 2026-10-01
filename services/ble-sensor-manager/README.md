# OMK BLEセンサマネージャー

このRaspberry Piホストサービスは、BlueZ/BleakでペアリングせずBLEアドバタイズを受信し、物理的な`device_key`をOMKの論理的な`sensor_id`へ対応付け、`omk/living-env-01/environment`のようなベンダー非依存のMQTT topicをpublishします。

このサービスはDashboardコンテナの外で動作し、Dockerはホストゲートウェイ経由でポート8787へ接続します。BlueZのDBusソケットや特権コンテナは不要なため、Bluetoothへのアクセスはホストサービスに分離されたままです。setupが導入する`omk-ble-api.service`とnftables規則により、ポート8787への入力はloopbackとDocker bridgeに限定します。このAPI自体には認証がないため、この制限を維持し、ブラウザからはDashboard backendを経由します。

Pi上で`scripts/setup-ble-sensor-manager.sh`を実行すると、virtualenvの作成とsystemd unitの導入・再起動を行えます。unitは`OMK_BLE_REGISTRY`と`OMK_NODE_REGISTRY`をそれぞれ`data/ble/sensors.json`と`data/ble/nodes.json`へ設定し、`data/ble`だけを書込み可能にしたまま`ProtectSystem=strict`を維持します。登録情報はこれらの可読なJSONへ保存します。

## ESP32 Node初回Wi-Fi設定

Nodeの初回Wi-Fi設定はBLE Sensor Managerでは行わない。一般利用者はNodeをGatewayへUSB接続し、[Dashboardのセットアップ](../../docs/user/esp32-node-setup.md)でsystem-managerにprebuilt書込みとWi-Fi設定を要求する。通常firmware導入済みNodeへの開発用Wi-Fi設定には`python3 scripts/provision_omk_node_via_usb.py`も利用できる。USB transportはNode IDを取得し、credential保存・software reboot・MQTT registration statusまでを確認する。通常のBLE scan、SwitchBot relay、MQTT Node registrationはこの操作から独立して継続する。

Nodeの`node_id`はeFuse由来の物理IDであり、`logical_id`はNode自身が測定値を`omk/<logical_id>/...`へ送る場合だけの論理IDである。`logical_id`は全Nodeで一意でなければならず、Gatewayは重複した登録要求・ACK・registration statusを拒否する。BLE relay専用Nodeはrelay topicに`node_id`を使うため、`logical_id`を登録する必要はない。

新品Gatewayでも、NodeがMQTT接続時に送るretained `registration/status`の`logical_id`から登録情報を復元する。Node自身のNVSを情報源とし、以前のGateway registryやACKは不要。IDのない旧firmwareのregistered statusも受理し、既存のLogical IDを保持する。`provisioned` statusは従来どおり登録を解除する。payloadと互換性の詳細は[MQTT仕様](../../docs/developer/data-and-mqtt.md#payload)を参照。

機器管理画面では登録済みSEN66 NodeのLogical ID変更と登録解除ができる。変更はNodeのACKを受けるまでGateway上の現在値を変更せず、解除はWi-Fi credentialを残したままNodeのlogical registrationとretained ACKを消去する。オフライン中の解除要求もretain付き設定で次回接続時に適用される。

`device_key`は物理的な識別子（`switchbot:<コロンを除いた小文字MAC>`）、`sensor_id`はOMKの論理的な識別子です。registryには`device_key`、`sensor_id`、`sensor_type`、vendor、model、location、`display_name`、`enabled`だけを保存します。最新の測定値、RSSI、受信時刻、生のBLEアドバタイズデータはruntime stateとして保持し、registryへは書き込みません。

## ESP32 Node relay入力

ESP32 NodeはGatewayの`sensor_id`、SwitchBotの機種判定、decoderを持たない。Nodeはmanufacturer data、service data、BLE address、RSSIをraw observationとして次の内部MQTT topicへpublishし、Gatewayがdirect BLEと同じdecoderで`device_key`を決定する。

```text
omk-relay/<relay_node_id>/ble/raw
```

このサービスはこのtopicを購読し、既存registryで`device_key`に対応するsensorを特定します。登録済みかつ`enabled=true`のenvironment、motion、contact、power sensorだけを、Gateway受信時刻を`measured_at`として通常のsensor topicへ`source: "relay"`と`relay_node_id`を保ったまま再publishします。未登録・disabledのdevice_keyは通常sensor topicへpublishせず、自動登録もしません。

direct BLEも同じ通常のsensor topicへ`source: "direct"`でpublishします。Gatewayは有効なdirect observationを受信してから30秒未満のrelayを抑止し、direct復帰時は同値でも最初のdirectを即時publishします。経路の冗長化とJSONL/Harvestでの重複排除は[`docs/decisions/ble-direct-relay-route-selection.md`](../../docs/decisions/ble-direct-relay-route-selection.md)を参照してください。

Dashboardの機器管理内のセンサ登録フローでは、未登録のBLEアドバタイズを発見・分類し、IDを提案した後、選択した表示名とlocationで登録します。`temperature_humidity_sensor`と`waterproof_sensor`には未使用で最小の`th-xxx`、`co2_sensor`には`co2-xxx`、状態センサには対応する`motion-xxx`（人感センサーとPresence Sensor Pro）/ `contact-xxx`を提案します。センサを交換した場合は、原則として新しい論理IDを発行します。

未知のSwitchBot BLEアドバタイズは、セットアップ中にmanufacturer dataとservice dataの生hexを付けて意図的に表示します。推測した値をpublishせずに、Meter Pro CO2、人感センサ、開閉センサを実機で検証できるようにするためです。

OMKでは製品名と内部modelを分離しています。SwitchBot MeterおよびMeter Plusは`temperature_humidity_sensor`、SwitchBot Meter Pro CO2は`co2_sensor`を使用します。旧来の`meter`、`meter_plus`、`meter_pro_co2`を持つ既存registryは互換的に読み込み、次回のregistry更新時に現行名へ書き換えます。

| OMK model | Dashboard表示名 | sensor type | 正規化後の値 |
| --- | --- | --- | --- |
| `temperature_humidity_sensor` | SwitchBot 温湿度計 | `environment` | `temperature_c`, `relative_humidity_percent` |
| `co2_sensor` | SwitchBot CO2センサー | `environment` | 温度、湿度、`co2_ppm` |
| `motion_sensor` | SwitchBot 人感センサー | `motion` | `motion_state` (0=不在, 1=検知) |
| `presence_sensor` | SwitchBot Presence Sensor Pro | `motion` | `motion_state` (0=不在, 1=検知), `battery_percent`, `light_level` |
| `contact_sensor` | SwitchBot 開閉センサー | `contact` | `contact_state` (0=閉, 1=開) |
| `waterproof_sensor` | SwitchBot 防水温湿度計 | `environment` | `temperature_c`, `relative_humidity_percent` |
| `plug_sensor` | SwitchBot プラグミニ | `power` | `power_w`, `switch_state` (0=OFF, 1=ON) |

`waterproof_sensor`は屋外専用ではありません。設置用途は`location`（例: 屋外、浴室）で表します。`humidity_percent`、`outdoor_meter`、`waterproof_meter`はOMKのschema/model名ではありません。

## 登録の削除と交換

管理画面の`/admin/sensors`では、編集画面から登録済みBLEセンサを`device_key`単位で登録解除できます。この操作はregistryの登録情報だけを削除し、JSONL、Parquet、Harvest retry queue、SORACOM Harvest Data、MQTTの過去履歴には一切触れません。削除された`sensor_id`は空くため、故障交換時は旧センサを削除し、セットアップモードで発見した新品へ同じ`sensor_id`を再使用できます。

`enabled=false`は一時的な停止であり、registryと`sensor_id`の占有を維持しつつruntime stateの更新だけを続け、MQTT publishを止めます。一方、削除後はregistryから完全に外れ、以後MQTT publishしません。周囲で同じ物理デバイスのBLEアドバタイズを受信し続けていれば、未登録candidateとして再び表示され、セットアップから再登録できます。

BLEアドバタイズはペアリングなしで継続受信します。`enabled=true`のenvironmentセンサは、最初に有効な値を受信した時点で即時publishし、その後は`device_key`ごとに最短10秒間隔でpublishします。runtime stateはBLE受信ごとに更新します。motionとcontactは状態変化時に即時publishし、加えて現在状態を最短10秒間隔でpublishします。これらの制限にはmonotonic clockを使用します。Plug Miniの電力も同じ10秒制限に従いますが、`switch_state`の変化時は即時publishします。`enabled=false`のセンサもruntime stateは更新しますが、MQTTはpublishしません。Plug Miniには`plug-xxx`を提案し、`power_w`と`switch_state`を含む`omk/<sensor_id>/power`をpublishします。

対応serviceのdevice typeを先に選び、登録済みmodel hintと矛盾したらunknownにします。serviceがないmanufacturer-only packetは、既存登録のhintに対応する形式だけを検証します。長さ・実測marker・正常値だけで新規機種を判定しません。MeterとMeter Pro CO2の観測済みmanufacturer形式も同じ方針です。CO2の2個体での実測は値形式の根拠であり、16-byte長の機種固有性を保証しません。

manufacturer-only packetは自動ではunknownになります。対応するmanufacturer decoderで再検証し、成立するmodelがちょうど1種類の候補だけに「候補（未確認）」と参考値を表示します。たとえば防水温湿度計では温度・相対湿度、人感センサーでは検知状態を確認できます。複数modelが成立するpacket、不正値、矛盾するservice dataには登録ボタンを出しません。参考値はcandidate APIとDashboardの確認専用で、利用者が機種を確認して登録するまでhint、MQTT通常topic、保存・集約には使いません。Meterの`0x54` / `0x69`、CO₂センサーの`0x35`、Motion Sensorの`0x73` serviceは自動識別を維持します。Motionは妥当なmanufacturer statusがあれば、その検知状態をservice値より優先します。firmware/broadcast modeやservice取得可否により、対応製品でも未認識となる可能性があります。不正値・矛盾は空valuesとし、登録・測定値publishに使いません。登録済み一覧の未識別packetは「機種・データ未確認」と表示します。根拠分類、移行上の注意、実機確認計画は[model evidence設計](docs/switchbot-model-evidence.md)を参照してください。

将来のclassifier変更では、[2026-08-20 SwitchBot raw capture監査](docs/switchbot-raw-capture-audit-2026-08-20.md)に記録した個体数・capture数・byteごとの根拠レベルも参照してください。

## Raspberry Piでの確認事項

Pi内蔵Bluetoothでは、SwitchBot BLEアドバタイズが`manufacturer_data[0x0969]`だけで報告されることがあり、Presence Sensor ProのScan ResponseをHCIレベルで取得できない実機差が確認されている。そのため`service_data`が空でもエラーではない。AtomS3 Lite Node relayはactive scanでScan Responseを取得でき、Presence Sensor Proのmanufacturer dataとfd3d service dataを結合したraw observationを実機確認済みである。新しいfixtureを取得する際、`raw_scan`は両方の構造を出力する。

公開前の追加実機確認では、利用者が新品microSDから標準Pi4 Gatewayを構築し、旧registryなしでPresence Sensor Proを初回登録できたと報告している。service dataの取得には環境差があるという上記の観測記録と、このfresh登録成功は区別して保持する。

```sh
PYTHONPATH=src .venv/bin/python -m omk_ble.raw_scan --seconds 30
```

サービスを起動する前に、`rfkill list bluetooth`と`bluetoothctl show`でアダプタを確認します。soft blockされている場合は、`sudo rfkill unblock bluetooth`、続いて`sudo bluetoothctl power on`を実行してください。セットアップスクリプトも可能な範囲でこれらを実行します。アダプタがない、または電源を投入できない場合は、導入を失敗させず警告を記録します。

過去のRaspberry Pi実機確認記録（今回の判定変更後の実機確認は未実施）:

- SwitchBot 温湿度計: BLEアドバタイズの受信、温度・湿度のdecode。
- SwitchBot CO2センサー: BLEアドバタイズの受信、温度・湿度・CO2 ppmのdecode（2台の実機で確認）。
- SwitchBot 人感センサー: BLEアドバタイズの受信、状態遷移、定期MQTT publish。
- SwitchBot Presence Sensor Pro: BLEアドバタイズの受信、在不在状態・battery・照度のdecode。
- SwitchBot 開閉センサー: BLEアドバタイズの受信、状態遷移、定期MQTT publish。
- SwitchBot 防水温湿度計: BLEアドバタイズの受信、専用temperature/humidity manufacturer layoutのdecode。
- SwitchBot プラグミニ: BLEアドバタイズのみからの電力・switch stateのdecode。公開されている`0x67`および国内実機で観測した`0x6a`のservice typeをmanufacturer推定より優先します。電力値はMSBのoverload flagをmaskしてから、0.1 W scaleを適用します。

### SwitchBot プラグミニ実機確認

Raspberry Piと実Plug Miniで、ペアリングなしのBLEアドバタイズ受信からDashboardのBLEセットアップ、`plug-001`登録、MQTT、JSONL保存、Harvest 1分集約、SORACOM Harvest Data送信までを確認した。内部modelは`plug_sensor`、sensor typeは`power`、Dashboard表示名は「SwitchBot プラグミニ」である。

manufacturer dataではstate byteのbit 7を`switch_state`として用い、0=OFF、1=ONを実機のOFF→ON操作と照合した。電力は公式仕様に従い`raw_power = ((byte10 & 0x7f) << 8) | byte11`、`power_w = raw_power / 10.0`でdecodeする。byte10のbit 7はoverload flagであり電力値から除外する。低負荷で`0x0034`=5.2 W、`0x0035`=5.3 W（アプリ表示約5.1〜5.2 W）、高負荷で`0x05f2`=152.2 W、`0x06c4`=173.2 W、`0x06cd`=174.1 W（実負荷約170 W）を確認し、低負荷・高負荷とも0.1 W scaleが成立した。

国内実機ではfd3d Service Dataとして`6a0064`を観測した。`0x6a`は参照した公式実装にもあるが、公式BLE仕様書の`0x67`とは根拠を区別する。どちらもPlug選択の根拠とし、12-byte manufacturer layoutは選択後の値検証に使用する。

国内Plug Miniの複数個体で12-byte manufacturerアドバタイズデータを確認している。byte 8は旧個体で`0x16`、新品個体で`0x10`を観測したため、固定classifierには使用しない。service dataの公開device type `0x67`または国内実測値`0x6a`で識別できる場合、state byte（index 7）と末尾2 byteから値をdecodeする。state byteのbit 7は`switch_state`、電力は`((data[10] & 0x7f) << 8) | data[11]`に0.1 W scaleを適用する。新品実機の`...8010370037`はON / 5.5 Wとして確認した。service dataがない場合は登録済みPlug hintを要求し、未登録ではunknownにする。`0x16` markerだけでの初回識別は廃止した。`0x10`および`0x16`の意味は現時点で断定しない。

Piで行指向のraw captureを取得するには、`PYTHONPATH=src .venv/bin/python -m omk_ble.raw_scan --seconds 30`を使用します。
