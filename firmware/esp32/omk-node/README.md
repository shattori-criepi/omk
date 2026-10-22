# OMK ESP32 Node

OMK NodeはAtomS3 Liteをproduction対象とする共通firmware基盤です。ボード固有のLCD、LED、ボタンはこの基盤に含めません。BLE中継（`ble_scan`）とSEN66計測は同じ基盤で共存できます。SEN66接続やBLE relay利用の有無にかかわらず、このfirmwareを標準とします。

この文書はfirmwareの開発・デバッグ・特殊復旧向けです。一般利用者の初回Nodeセットアップは[「3-2. SEN66 Nodeをセットアップする」](../../../docs/user/esp32-node-setup.md)を使用します。既存Nodeの正式なfirmware更新は、[「OMK Nodeを更新・再設定する」](../../../docs/user/node-maintenance.md#ファームウェアを更新する)に従い`scripts/flash-omk-node.sh`を使用します。ここでは、実装済みかつ実機で確認済みのDiscovery、USB Serial/JTAG Provisioning、ESP-WIFI-MESH、SEN66、BLE relay、MQTTを記録します。3台のAtomS3 Liteでraw BLE relay、SEN66、Mesh/MQTTの同時動作を確認済みです。

## 有線センサの識別（M4）

SEN66はI2C ACKだけでなく、CRC等を検証したProduct Nameの完全一致後に初期化する。共通driver registry、再探索、将来のI2C/UART/analog sensor追加時の契約は[Node sensor discovery設計](../../../docs/decisions/node-sensor-discovery.md)を参照。analogは汎用profileによる明示設定を前提とし、ADC値から機種を推定しない。この変更のホストテスト・buildは実施したが、新しい識別・復旧シーケンスの実機試験は未実施。

## 対応環境

- PlatformIO: `espressif32@7.0.1`（ESP-IDF 6.0.1）に固定
- フレームワーク: ESP-IDF
- Bluetooth: Bluedroid、BLE-only controller、BLE 4.2 legacy advertising
- production対象: AtomS3 Lite

BLE 4.2 legacyを選ぶのは、Discovery v1が31-byte legacy advertisingとBluedroidのraw GAP APIを使うためです。NimBLEまたはextended advertisingへ切り替える場合はプロトコル改訂が必要です。AtomS3 Liteでは8 MB Flash設定を使用します。

`CONFIG_COMPILER_DISABLE_GCC15_WARNINGS=y`はESP-IDF 6のGCC 15互換設定です。ESP-IDF本体を修正したり、広範な`-Wno-error`を追加したりしません。

## USB Serial/JTAG Provisioning protocol

AtomS3 Liteの通常firmwareはUSB Serial/JTAGをprimary consoleとして使用する。この同じUSB接続で、通常起動中にversioned JSON Lines protocolを処理する。未設定Wi-Fi時にもTemporary SoftAPを開始せずtransportは常時起動するため、物理ボタン、USB抜き差し、Gatewayの`wlan0`切替は不要である。

Gatewayでは次を実行する。1台接続時はSSID/PSK/Node IDの手入力は不要。CLIはby-idとttyACMを列挙し、realpathでaliasをまとめ、ttyACMまたはEspressif by-idの全候補を`identify`する。旧版を含む応答Nodeが0台なら失敗、1台なら自動選択、2台以上なら明示指定を求めて停止する。異なる物理portが同じnode_idを返す場合も停止する。

```bash
python3 scripts/provision_omk_node_via_usb.py
```

開発・再現試験でGateway Wi-Fi credentialだけを削除して未設定状態へ戻す場合は、対象Nodeを必ず明示して実行する。これはfactory resetではなく、PoP、identity、registration、logical ID、diagnosticsを削除しない。

```bash
python3 scripts/provision_omk_node_via_usb.py --device /dev/serial/by-id/... --clear-wifi
```

複数台のときは`--device <port>`または`--node-id <node_id>`で対象を選ぶ。両方指定した場合はidentify結果が指定IDと一致しなければ停止する。`--clear-wifi`は引き続き明示対象を必須とする。by-idを推奨するが、一時的なtty名でも操作用に開いた同じ接続で再identifyし、選択時のnode_idと一致するまでcredentialを送信しない。

USB専用protocolはv2。`set_wifi`と`clear_wifi`には12桁小文字hexの`expected_node_id`が必須で、firmwareは自身のnode_idと一致しなければ保存・消去・再起動予約の前に拒否する。欠落・不正値も拒否し、v1へのfallbackは行わない。Nodeは検証済み`set_wifi`受信後、既存の`wifi_station_save_credentials()`でFlash保存と read-back照合を行い、`accepted`応答を返してからsoftware rebootする。credentialや passwordはログ・応答へ含めない。再起動後は既存のSTA/MQTT registration起動経路をそのまま使用する。

実機ログが同じstreamへ流れるため、CLIのper-command timeout既定値は10秒とする。設定完了は、CLIの`accepted`後にMQTT topic`omk/node/<node_id>/registration/status`で`provisioned`または`registered`を確認して判断する。

### v1からの更新

GatewayのCLI/system-managerとNode通常firmwareを両方更新する。system-manager更新後はサービスを再起動する。既存Nodeの通常firmware更新は[「OMK Nodeを更新・再設定する」](../../../docs/user/node-maintenance.md#ファームウェアを更新する)の通常更新手順を使い、保存済みcredential、Wi-Fi、Logical IDを維持する。新品Nodeの初回導入は[「3-2. SEN66 Nodeをセットアップする」](../../../docs/user/esp32-node-setup.md#2-dashboardからセットアップする)を標準とする。使用済みNodeを新Gatewayへ設定し直す場合は[「OMK Nodeを更新・再設定する」](../../../docs/user/node-maintenance.md#既存nodeを再セットアップする)の再セットアップ手順を使う。この再セットアップはcredentialを置換し、Wi-Fiを再設定、Logical IDを解除するため、通常更新とは区別する。探索用identifyだけはv1を受け付け、旧firmwareも台数に数える。旧firmwareを選択した場合は操作用v2確認で停止し、credentialを送信しない。旧CLIから新firmwareへのv1状態変更要求も拒否する。node_id、logical_id、保存済みWi-Fi設定の形式、BLE Discovery/MQTT protocol v1は変更しない。新しいidentity guardの実機試験は未実施で、従来の実機確認記録はv2の安全性確認を意味しない。[検証手順](../../../docs/decisions/usb-serial-provisioning.md)を参照する。

## Node ID

`node_id`はfactory eFuseのbase MACを基準に生成します。ファームウェアは`esp_efuse_mac_get_default()`で6 bytesを取得し、順に64-bit FNV-1a（offset basis `14695981039346656037`、prime `1099511628211`）を計算して、下位48 bitsを12桁の小文字16進数として使います。Wi-Fi/BLEの初期化状態や MAC種別に依存しないため、書込みPCと実行中Nodeで同じIDになります。

Node IDの計算にはBluetooth MACではなくeFuseのbase MACを使用します。

## Factory secretとPoP

旧Security 1方式のPoPとして生成した256-bit random secretは、現在もfactory flashとNVS保存の処理に残っています。USB Provisioningではこのsecretによる認証は行いません。firmwareに共通secretを埋め込まず、BLEアドバタイズ、通常ログ、Wi-Fi設定、MQTT設定にも含めません。

初回factory flashでは、custom `factory_secret` partitionに`OMKP`の4 bytesと raw 32-byte secretを置きます。最初の起動でファームウェアは以下を行います。

1. `factory_secret`を検証して読み取る。
2. NVS namespace `omk`、key `prov_pop`へ32-byte blobとして保存する。
3. `nvs_commit()`後にNVSから読み戻して一致を確認する。
4. 成功確認後だけfactory partitionを消去する。

この順序により電源断が途中で発生しても、factory recordまたはNVSの少なくとも一方から再実行でき、secretを失いません。通常のfirmware updateはNVSのsecretを保持します。secretの置換は、下記の明示的な再セットアップでのみ行います。

PC側の対応情報は`data/provisioning/nodes/<node_id>.json`に保存します。これはmode `0600`、Git ignore対象であり、secretを標準出力やリポジトリへ出しません。旧Security 1 APIでは32-byte raw secretを64文字のlowercase hex文字列へ変換して使用していましたが、現在のUSB登録経路では使用しません。

## Factory flashとpartition table

`scripts/flash-omk-node.sh atom-s3-lite <port>`は通常更新専用です。eFuse base MACからNode IDを算出し、`data/provisioning/nodes/<node_id>.json`が存在し、Node ID・board・64桁hex secretを含む形式が正しい場合だけfirmwareをuploadします。credentialの内容とfactory partitionは書き換えません。保存credentialがない、不正、読取り不能の場合は、firmware書込みとsecret生成より前に停止します。Gatewayのcredentialを失った使用済みNodeは、下記の明示的な再セットアップを選択できます。バックアップは必須ではありません。

新品・未ProvisioningのAtomS3 Liteであると利用者が確認できる場合だけ、`scripts/flash-omk-node.sh --initial-setup atom-s3-lite <port>`を使用します。この明示的なopt-inでのみNode固有secretとcredentialを作成し、factory partitionを書き込みます。既存credentialがある場合は拒否します。USB `identify`はNode ID・protocol version・Wi-Fi設定状態を返しますが、PoP不存在や未使用状態を証明しません。ROMのchip/MACにも使用歴はなく、安全なfresh自動判定には使えません。状態不明なら初回オプションを使わず停止してください。既存Nodeへ誤って初回オプションを指定するとPoPを変更し得るため、credential紛失時の救済には使用しません。

`scripts/flash-omk-node.sh --reinitialize atom-s3-lite <port>`は、使用済みNodeを意図的に新規セットアップ相当へ戻す破壊的な操作です。旧Gatewayのcredentialは不要です。通常更新・`--initial-setup`とは別経路で、新PoPをGatewayへ保存し、NodeのPoPを置換、Logical IDとWi-Fi設定を消去してから、現在のGatewayの`omk-ap`設定をUSB protocol v2で投入します。Node IDはeFuse MAC由来のままで、SEN66等は通常起動で再検出します。完了後はDashboardでNode/接続センサのLogical IDを登録し直します。

このCLIはsource build後、Dashboardと同じsystem-managerのsetup実装を使います。`services/system-manager/.venv/bin/python`（esptool導入済み）が必要で、別環境は`SYSTEM_MANAGER_PYTHON`で指定します。Gateway AP設定へのアクセスと`mosquitto_sub`も必要です。CLI実行中はDashboardからのUSB探索と競合しないようsystem-managerを停止し、完了後に再起動してください。実機は1台だけ接続し、処理中は差し替えません。

再セットアップ専用factory recordは`OMKR` + 32-byte PoPです。通常の`OMKP` importと区別し、ネットワーク開始前に`omk/registered`・`omk/logical_id`、`omk_net/gw_cred`をクリアし、`omk/prov_pop`を置換してread-backします。旧ESP-IDF STA設定がlegacy migrationで復活しないよう、`omk_net/reset_sta`でSTA設定のクリアを次のWi-Fi初期化まで引き継ぎます。ESP-IDF APIでSTA設定だけをクリアし、NVS namespace全体やflash全体は消去しません。診断情報等の無関係なNVSは保持します。すべてのimport処理が成功するまでfactory recordを保持するため、途中の電源断では再実行できます。

Gatewayは書込み前に新credentialをmode 0600でatomic保存し、`setup_state=reinitialize_pending`を記録します。失敗後はファイルを削除せず、同じ`--reinitialize`で再試行します。同じPoPでfactory recordを再書込みし、firmware更新後にUSBの`verify_reinitialize`で新PoP・Wi-Fi未設定・Logical ID未登録を確認してからWi-Fiを投入します。新しい`provisioned` statusを受信して初めてpendingを解除します。pending中の通常更新は拒否します。PoPは既存方式の保存情報であり、USB provisioning自体をPoP認証へ変更するものではありません。

Dashboardの再セットアップも専用APIと確認画面を使用します。配置済みprebuiltが`verify_reinitialize`未対応なら、書込み前に停止します。対応するprebuilt packageはsource commit後に既存のpackage生成手順で更新してください。未commitのsourceを試験するCLIは、ローカルbuildから一時packageを作り、公開prebuiltを書き換えません。

rawな`pio run -t upload`やesptool eraseは正式更新手順ではありません。

通常更新の書込み先はbootloader（`0x0`）、partition table（`0x8000`）、app（`0x10000`）です。現行partition tableのNVS（`0x9000`〜`0xefff`）とfactory secret（`0xf000`〜`0xffff`）を含まず、Wi-Fi credentialとLogical IDを消去しません。通常起動もNVS全消去を行いません。DashboardのUSB setupも既存Nodeではこの3領域だけを書き込みますが、その後にGatewayのWi-Fi情報を設定し直す処理があります。既存Wi-Fi設定をそのまま保持するfirmware更新には、上記の通常更新手順を使います。

登録済みNodeはMQTT接続時にNVSの`logical_id`をretained `registration/status`へ含めるため、新品Gateway・新brokerでも再登録操作や旧ACKなしでLogical IDを復元できます。これは通常更新scriptのcredential検証とは別の仕組みです。更新済みNodeの状態復元自体に旧Gatewayのファイルは必要ありません。`provisioned`状態ではIDを出力せず、再接続時に設定ACKを作り直すこともしません。

実機確認は、通常更新後にNodeが既存Wi-Fi設定で再接続し、従来の`omk/<logical_id>/sen66`へ送信を続けることを確認します。続いて同じ接続先設定の新品Gateway（空Node registry・旧ACKのないbroker）へ接続し、`omk/node/<node_id>/registration/status`に`registration_state=registered`と期待する`logical_id`があること、Gatewayの`GET /api/nodes`とDashboardに同じIDが出ることを確認します。Nodeの再接続とGateway service再起動後もIDが保持され、再接続だけではACKが送信されないことも確認します。運用中Gatewayのregistryやretained messageを削除して試験しないでください。

通常更新・初回セットアップの書込み前確認フローは次のとおりです。

1. build後、指定portのesptool `read_mac`出力から`Chip is ESP32-S3 ...`とeFuse base MACを確認する。chip情報なし・不正・曖昧な情報、非対応chipは停止する。
2. MACは形式を検証し、小文字へ正規化する。同一MACの重複は許容し、異なるMACが複数、0件、不正なMACは停止する。
3. `--initial-setup`を明示した初回だけsecret・credential・`OMKP` + 32-byte secretのrecordを一時ファイルに準備する。factory書込み直前にchipと最初のMACとの一致を再確認し、成功後にcredentialを既存ファイルを上書きしない方法で永続保存して`--chip esp32s3 write_flash 0xf000`を実行する。
4. firmware upload直前にも同じchip・MACを再確認する。既存credentialがある再flashでもこの確認を行う。PlatformIOの既存board設定もuploaderに`--chip esp32s3`を渡す。

確認失敗だけで中止した初回操作では新規credentialを残さず、一時ファイルを削除します。実際にfactory書込みを試みた後は、部分的に書けた可能性があるためcredentialを保持します。factory書込み成功後にupload前の確認で停止した場合は、元のNodeだけを接続し、portの再出現とパスを確認して再実行してください。既存credentialを使い、factory書込みを省略してfirmwareをuploadします。factory書込み自体が失敗した場合は、再実行だけではfactory recordを再書込みしないため、credentialを保持したままfactory provisioningの状態を確認する必要があります。

この確認はchip familyと各確認時点のMAC一致を検証するもので、AtomS3 Liteという製品の認証ではありません。別のESP32-S3 boardは自動拒否できません。USB descriptor、VID/PID、MAC OUI、flash容量から製品を推定しません。標準対応boardはAtomS3 Liteであり、利用者が実物と指定portを確認します。

確認と書込みは別のesptool接続です。最後の再確認から書込み接続まで（PlatformIO uploadの準備時間を含む）の差し替えを原子的に防ぐものではなく、同じMACを報告する機器も区別できません。対象NodeだけをUSB接続し、処理中は差し替えないでください。resetによるUSB再列挙でportが利用できなければ安全側に停止し、別のttyへ自動追従しません。再出現を待ち、元の機器とportを確認して再実行します。

実機確認は以下を利用者が実施します（自動テストはfake toolを使用し、USBへアクセスしません）。

- AtomS3 Lite 1台で`ESP32-S3 chip confirmed`表示、初回factory recordの正常書込み、firmware uploadと起動を確認する。生成物・実機のpartition tableも下記のとおり確認する。
- 同じNodeの再flashでcredentialが維持され、factory書込みを省略し、firmware uploadが成功することを確認する。
- 別ESP32 familyでは`Unsupported chip`で停止し、factory書込み・uploadとも開始しないことを確認する。
- USB reset・再列挙後の再確認が通るか、port消失時には停止して再試行できるか確認する。
- 可能なら書込みを許可できる試験用の別ESP32-S3 boardで、chip確認を通過することを確認する。製品識別による自動拒否はないため、実行するとOMKを書き込む点に注意する。

partition layoutは次のとおりです。

| Partition | Type/subtype | Offset | Size |
| --- | --- | ---: | ---: |
| `nvs` | data / nvs | `0x9000` | `0x6000` |
| `factory_secret` | data / `0x40` | `0xf000` | `0x1000` |
| `factory` | app / factory | `0x10000` | `0x200000` |

`platformio.ini`の`board_build.partitions = partitions.csv`とESP-IDF側のcustom partition設定の両方が必要です。PlatformIOのデフォルトsingle-app tableでは`0xf000`は`phy_init`であり、custom tableが実機へ書かれていない状態でそこへ secretを書き込むことは危険です。書込み前に生成物・実機双方のpartition tableを確認します。

### 開発時だけの登録状態reset

Dashboard登録フローを再試験する場合だけ、次を明示的に実行します。

```bash
./scripts/reset-omk-node-registration.sh atom-s3-lite /dev/ttyACM0 --confirm
```

このスクリプトは一時的な開発用imageをflashし、NVS namespace `omk`の`registered`と`logical_id`だけをeraseしてcommitします。両keyがread-backで存在しないことを確認した後、通常firmwareを自動的に戻します。NVS partitionの全消去、Wi-Fi credentials、`prov_pop`、`factory_secret` partitionへの操作はしません。通常firmwareにはこの操作へ到達するruntime経路はありません。

### 開発時だけのWi-Fi credential投入

Wi-Fi Provisioningを介さずBLE relayなどを検証する場合だけ、次を実行します。

```bash
./scripts/set-omk-node-wifi.sh atom-s3-lite /dev/ttyACM0
```

SSIDは通常入力、PSKは非表示入力です。どちらもrepository、build flag、ログ、通常のfirmware sourceには保存しません。一時imageはOMK専用NVS credential storeへGateway SSID/PSKを書込み、read-backで一致を検証します。ESP-WIFI-MESHが利用するruntime STA configurationとは共有しません。さらに書込み前後で`omk/prov_pop`が32 bytesのままであることを確認し、`registered`、`logical_id`、factory secret、Node IDは変更しません。スクリプトは直後に通常firmwareを書き戻し、credentialを含む一時headerと専用build directoryを削除します。

credential分離版へ更新した既存Nodeは、旧`WIFI_IF_STA`にMesh parent情報が残っていると安全に自動migrationできない。この場合はGatewayへNodeをUSB接続し、次を実行して専用NVS storeへ再投入する。

```bash
python3 scripts/provision_omk_node_via_usb.py --device /dev/ttyACM0 --profile omk-ap
```

`USB provisioning confirmed for node_id=...`を確認する。Nodeの物理ボタン操作やUSB抜き差しを通常のreboot手段として前提にしない。

`set-omk-node-wifi.sh`は開発専用のone-shot操作であり、production firmwareの通常bootやMQTT APIからは実行しません。USB再Provisioningは通常firmwareでも利用できます。

## Discovery BLE v1

通常bootのNodeは、Wi-Fi設定の有無にかかわらず次のService UUIDのraw legacy advertisingを出します。設定済みの場合はMeshおよびMQTT registrationと並行して動作し、GatewayがNodeを発見・状態確認する用途に使います。Wi-Fi未設定Nodeは未設定状態を通知しながら、USB Serial/JTAG経由の初回登録を待ちます。

- Service UUID: `7d2a4d90-7b64-4e3a-9f37-95e77d7b5101`
- Service Data: 厳密に10 bytes `protocol_version(1) | provisioning_state(1) | capabilities(2, big-endian) | node_id(6)`
- Protocol version: `1`
- Provisioning state: `0` = unregistered、`1` = provisioned、`2` = registered。保存済みWi-Fi credentialと`omk/registered`から決定する。
- Capability bits: `ble_scan = 0x0001`、`sen66 = 0x0002`。複数bitの組合せを許容する

Service Dataには`site_uuid`、SSID、Wi-Fi password、PoP、MQTT credential、SORACOM情報その他の秘密情報を載せません。AtomS3 Liteのadvertising v1はGateway 管理画面で未登録OMK Nodeとして実機検出済みです。

旧方式では、次のControl GATT serviceを再Provisioningの開始要求に使用していました。現在のfirmwareにはこのGATT serviceも開始処理もありません。再設定にもUSB Serial/JTAGを使用します。以下は旧実装の識別情報です。

- Control Service UUID: `c1347091-4268-2fb1-884a-7d019a432154`
- START characteristic UUID: `c1347091-4268-2fb1-884a-7d019a432155`
- Property: write only。payloadは1 byteの`0x01`だけを受理する

旧実装では不正長、値、prepared writeをGATT errorとして返し、GATT callbackから固定長4のFreeRTOS event queueを経由して`node_state_task`へ開始要求を渡していました。

## SwitchBot raw BLE relay

通常bootのNodeはOMK Discovery advertisingと並行してactive scanを行います。Scan RequestはScan Response取得のためだけに使い、NodeはSwitchBot機器へ接続・pairingしません。SwitchBot Company ID `0x0969`のmanufacturer dataとfd3d service dataをraw observationとしてrelayします。Nodeは機種判定、decode、`device_key`生成をせず、`th-001`のようなGatewayの論理sensor IDも持ちません。

raw relayはdeviceごとに原則最短10秒間隔で次のGateway入力topicへQoS 0、retain falseでpublishします。active scanでADV後2秒以内にScan Responseが加わりmanufacturer dataとservice dataの両方が揃う場合だけ、fragment completenessの更新として1回の即時追加publishを許可します。counter、RSSI、値の変化だけでは10秒以内に再送しません。

```text
omk-relay/<relay_node_id>/ble/raw
```

```json
{"protocol_version":1,"relay_node_id":"112233445566","ble_address":"020000000001","rssi":-45,"manufacturer_data":[{"company_id":2409,"data":"..."}],"service_data":[{"uuid":"0000fd3d-0000-1000-8000-00805f9b34fb","data":"..."}]}
```

Gateway BLE Sensor Managerがdirect BLEと同じdecoderで`device_key`を作り、既存registryで論理sensor IDに対応付けます。登録済みかつenabledなenvironment、motion、contact、power sensorだけを通常のsensor topicへ再publishします。Nodeの`logical_id`、BLE physical address、Gatewayの`device_key`、registryのsensor IDは別概念です。

rate-limit状態はメモリ上だけに保持し、同時にactiveとして追跡するBLE addressは固定16台までです。60秒以上観測されないaddressのslotは新しいdeviceのために再利用しますが、activeな16台をLRU方式で追い出すことはありません。Gateway decoderが対応するMeter / Meter Plus、Meter Pro CO2、防水温湿度計、人感、Presence Sensor Pro、開閉、Plug Miniを同じrelay経路で扱います。Gateway direct BLEとの冗長化・sensor特定・30秒fallback選択の理由と実機確認は[`docs/decisions/ble-direct-relay-route-selection.md`](../../../docs/decisions/ble-direct-relay-route-selection.md)を参照してください。

## 旧BLE Provisioning（廃止済み・履歴）

> この節と次節は旧方式の履歴であり、以下のフロー・API・互換経路は現在の実装を説明するものではない。BLE Security 1、Control GATT、SoftAPを使った登録処理は削除済みで、初回登録と再設定はUSB Serial/JTAGで行う。

ESP-IDF 6ではBluetooth controllerの同一boot内でのdeinit後再initを前提にしません。そのためDiscovery BLEとnetwork provisioning BLEを同時に動かしたり、同一boot内で所有権移譲したりしません。起動時のfactory secretから`omk/prov_pop`への移送後、保存済み Wi-Fi STA credentialで次のように分岐します。

```text
factory PoP import
        |
        +-- Wi-Fi credentialなし
        |     -> Espressif Provisioning BLE
        |     -> Security 1 + Node固有PoP
        |     -> Wi-Fi設定と接続
        |     -> Provisioning終了後にreboot
        |
        +-- Wi-Fi credentialあり
              -> Wi-Fi STA
              -> MQTT registration
              -> OMK Discovery BLE
```

`wifi_station_init_network_core()`がnetifとdefault event loopを初期化し、ESP-WIFI-MESH通信層がdefault STA netifを作成してから、`wifi_station_prepare()`がWi-Fi driverとFlashに保存されたSTA credentialを初期化・確認します。設定済みNodeのSTA netifはESP-WIFI-MESH通信層が単独で所有し、root時はGateway向けSTA、child時はinternal IP mesh STAへ切り替えます。これによりdefault STA netifを二重に初期化しません。

旧Control GATT `0x01`は、Wi-Fi設定済みNodeの再Provisioningに使用していました。PoPが有効なら`omk/next_boot_mode`へ`PROVISIONING`を保存・commitして再起動し、次bootの先頭でflagを消去・commitする方式でした。現在はこの経路を使用しません。

Discoveryを使う通常bootでのClassic BT memory releaseはboot中に一度だけ行い、BLE-onlyとWi-Fi STAの併用には影響しません。

## 旧Security 1 BLE Provisioning（廃止済み・履歴）

Wi-Fi未設定bootのProvisioningは`network_prov_scheme_ble`と`NETWORK_PROV_SECURITY_1`のみを使用します。Security 0 / Security 2へfallbackしません。`CONFIG_ESP_PROTOCOMM_SUPPORT_SECURITY_VERSION_1=y`が必要です。

- Provisioning Service UUID: `c2f08e31-75fd-4f81-9e6d-4f89a3bc1d27`
- Service name: `OMK_<12 hex node_id>`（例: `OMK_112233445566`）
- PoP: 前節のNode固有secretをlowercase hexへ変換したもの

Provisioning BLEのservice nameは`OMK_<node_id>`です。`node_id`は12桁lowercase hexの安定した内部識別子ですが認証情報ではありません。最終的なNode所有確認は、Security 1と Node固有PoPによるhandshakeで行います。Gateway側はfactory flash時に作成したNode credential storeを使ってPoPを取得します。PoP、SSID、Wi-Fi passwordはadvertising、通常ログ、MQTT、APIへ載せません。

`NETWORK_PROV_WIFI_CRED_SUCCESS`でWi-Fi接続成功を記録し、`NETWORK_PROV_END`でProvisioning managerをdeinitします。その後、event callbackから直接ではなくFreeRTOS task経由で`esp_restart()`します。再起動後は保存済みcredentialを検出するため、Wi-Fi STA、MQTT registration、OMK Discovery BLEの通常bootへ進みます。

Control GATT STARTは既存Wi-Fi credentialを消去せずに再Provisioningを明示する互換操作です。この経路でもSecurity 1 BLE serviceを開始します。

AtomS3 LiteではPC側credentialのPoPを用いたSecurity 1 session、SSID/passwordの保存、直後のWi-Fi接続を実機で確認済みです。SetConfigおよびApplyConfigはいずれも成功しました。

### Windowsでの手動確認

WSLには通常BLE adapterが渡らないため、Security 1 clientはWindows側のPython 3.13 環境で実行します。Espressif公式`esp_prov`コードと`bleak`、`protobuf`、`cryptography`を使います。service nameが取得できない場合もBluetooth MACで対象を識別して確認できます。Wi-Fi未設定Nodeは起動直後からProvisioning BLEをadvertiseするため、Control characteristicへの事前writeは不要です。

## 通常bootのWi-Fi STA

`wifi_station_prepare()`は保存済みcredentialの有無を確認し、Wi-Fi driver初期化を一度だけ行います。netif/event loopとdefault STA netifはその前に一度だけ作成する。設定済みの場合はESP-WIFI-MESHが保存済みcredentialをrootのGateway接続に用い、childは自動選択されたparent経由のinternal IP networkingを使います。`IP_EVENT_STA_GOT_IP`はroot/childのどちらでも既存MQTT起動に使われます。SSID/passwordや導出したMesh credentialはログしません。Wi-Fi初期化が失敗した場合は、Meshを開始せず、既存の安全側エラー処理に従い、Discovery BLEは可能な範囲で継続します。

Wi-Fi未設定bootでもOMK Discovery BLEを開始します。Wi-Fi設定は常時起動するUSB Serial/JTAG transportで受け付けます。

## ESP-WIFI-MESH（正式通信層）

AtomS3 Liteをproduction対象として、共通NodeはESP-WIFI-MESH、SEN66、BLE scan / SwitchBot relay、MQTTを同時に実行する。Node自身が計測・BLE relay・Wi-Fi Mesh中継を兼ねるため、SEN66なしのrelay Nodeと、SEN66ありのrelay Nodeを別firmwareに分けない。

保存済みGateway SSID/PSKからMesh IDとMesh AP passwordをruntime導出する。Gateway credentialはOMK専用NVS storeに保持し、ESP-WIFI-MESHがchild parent選択で変更するruntime STA configurationとは共有しない。root/parent/child、Node別SSID、固定IP、manual parentの設定はない。rootはGateway APへ接続し、internal networkのDHCP/DNS/NAPTを提供する。childは自動選択されたparent経由でIPを得て、既存の`IP_EVENT_STA_GOT_IP`起点で通常TCP MQTTを`mqtt://192.168.50.1:1883`へ接続する。GatewayにMesh daemonや特別なrouting設定は必要ない。市販Wi-Fi中継機は必須ではないが、実際の到達性はNode配置と電波条件に依存する。AC電源で常時動作させる。

topologyはrootを頂点とするtreeで、childはparent経由でrootへ到達する。parentまたはrootの喪失時にはMeshが自動再構成する。弱いroot uplinkでparent/MQTT切断が増加した場合は、標準APIによるroot再選出を要求する。Nodeの参加・復帰や低RSSIの継続だけでは要求しない。[現行の再選出policyとlivenessとの関係](../../../docs/decisions/esp-wifi-mesh-node-networking.md#現行のroot再選出とlivenessの関係)を参照する。起動時は`esp_wifi_start()`、`esp_mesh_init()`、`esp_mesh_start()`の成功後にだけinternal-netif receive taskを開始する。Mesh初期化前に`esp_mesh_recv()`を呼ばない。

### AtomS3 Lite運用RSSI profileと実機検証（2026-09）

AtomS3 Liteの正式な運用profileは`high=-78`、`medium=-80`、`low=-82 dBm`とする。`sdkconfig.atom-s3-lite`は`platformio.ini`で共通の`sdkconfig.defaults`の後に読み込まれるため、AtomS3 Lite固有の設定はこのfileで定義する。

`low=-82 dBm`は新規parent選択時に弱い候補を除外する下限として有効である。一方、すでに接続済みのparentのRSSIがこの値を下回っただけでは、標準self-organized Meshは能動的なparent再選択を保証しない。このためOMKは、起動・再接続時の弱い親の回避とparent消失時の自動復旧にはこのprofileを用いるが、安定リンクに対するcustom RSSI roaming、manual parent選択、parent固定は実装しない。継続的な通信障害と低RSSIが確認された場合だけ、将来のparent selection手段を別途検討する。

実機では、1階、階段付近、2階に配置した3台のNodeで確認した。以下の配置名は試験時の位置関係を表し、Node IDや役割の固定設定ではない。

- ESP-IDF標準の複数root許可状態では、同一Gateway BSSIDに2 rootが180秒超共存した。全3台へ`esp_mesh_allow_root_conflicts(false)`を含む同一firmwareを書き込むとrootは1台になり、再発しなかった。
- 通常の安定topologyは、階段フロアNodeがroot/layer 1で、1F階段下Nodeと2F SEN66 Nodeがともにlayer 2 childとなった。
- 階段フロアNodeを停止すると、2F SEN66 Nodeがrebootなしでroot/layer 1へ昇格し、1F階段下Nodeがlayer 2へ再接続した。SEN66の`root_switch_count`は増加し、MQTTは一時切断後に復帰した。階段フロアNodeを戻してもsecond rootは発生せず、2F rootを維持したまま階段フロアNodeがlayer 2 childとしてjoinした。
- その後1F階段下Nodeを再起動すると、`2F SEN66 root/layer 1 → 階段フロア layer 2 → 1F階段下 layer 3`の3層経路を確認した。扉閉鎖時にroot直結候補は約`-84～-86 dBm`、選択された階段フロアparentは約`-77 dBm`であり、multi-hopが動作した。

このフェイルオーバー中、SEN66 MQTT publishの最大欠測は約51秒で、その後約10秒周期へ復帰した。ESP rebootはなくMQTT切断は観測されたが、SEN66 recovery counterは増えず、registration statusの`sen66_rc`と`sen66_to`も出なかった。従って当該時点のsensor measurementが正常だったことは断定できず、過去の恒久的SEN66欠測の根本原因も未確定である。一方でMesh/MQTT不安定化に伴うpublish gapは確認された。60秒liveness watchdogはmeasurement停止時にsensor再初期化を試み、以後の`sen66_rc`（recovery総数）と`sen66_to`（liveness timeout数）で区別して追跡する。

### Mesh diagnostic status

30秒ごとに次の非retain topicへpublishする。

```text
omk/node/<node_id>/status
```

2026-10-13の復旧改善では、`mqtt_connected`だけで`healthy`としない。既存`sen66_tx`（センサなしでも起動）から`omk-liveness/<node_id>`へ最大30秒に1回、payload `1`、QoS 1、非retainで送る。監視用message IDに一致する`MQTT_EVENT_PUBLISHED`（PUBACK）だけを通信進捗として記録する。登録用PUBACK、QoS 0、enqueue成功、期限切れ削除は正常判定に使わない。監視topicはGatewayの通常収集`omk/#`の外に置き、不要な保存・変換を増やさない（PUBACKにsubscriberは不要）。

未確認監視送信は最大1件。切断中は新規投入せず、再接続しても未確認IDを保持する。対応するPUBACKまたは`MQTT_EVENT_DELETED`で解放する。enqueue失敗も30秒間隔で再試行する。監視のenqueueとID確定を同じMQTT recursive mutex内で行い、PUBACK/削除イベントがID確定を追い越さないようにする。先行イベント用の固定件数journalは廃止した。mutexがbusyなら待たずに見送り、次回の監視周期で再試行する。MQTT内部custom event queueは使用しない（既存のevent衝突回避を維持）。

| 状態／条件 | 遷移・動作 |
| --- | --- |
| parent/IP/MQTT接続＋90秒未満の監視PUBACK | `healthy` |
| 接続済みでもPUBACKが90秒途絶 | `mqtt_wait`（budget消費後は`budget_hold`） |
| 上記停滞が180秒＋Node ID jitter 0〜120秒継続 | 子Nodeのみ`restart_pending`。正常時最後のACKから約270〜390秒＋監視周期1秒 |
| 再起動後も通信不能 | budgetを消費したまま、起動後600秒＋jitter経過後に低頻度で再起動を再試行。以降も各bootで待機し、永久holdを避ける |
| 子Nodeがparent接続後もIP未取得 | `wait_ip`。180秒で親再選択、以後300秒間隔。600秒＋jitterで共通budgetによる再起動候補。自分の親再選択による切断・再接続ではIP期限を延長しない。Root昇格／IP取得で解除 |
| 実通信による`healthy`が900秒連続 | NVS clear・read-back成功後に通常budgetを再arm |
| NVS読込／保存／検証失敗 | restart禁止。時間経過では解除しない |

独立`mesh_recovery`タスクが1秒ごとにRAM snapshotから既存arbiterを実行し、MQTT mutex、送信処理、statusのdefault event loopを待たない。Mesh再選択・root再選出はdefault event loopへ最大1件を渡し、API失敗の再試行間隔30秒、既存のgrace/cooldownを維持する。子NodeのMesh actionが180秒以上進まない場合は、イベント投入失敗（queue full／メモリ不足）も含めて監視し、孤立600秒＋jitter後には再起動できる。投入済みフラグと進捗停止の起点は分け、投稿失敗だけでは起点を消さない。RootはMQTT停滞だけでは再起動せず、既存のrouter再接続・リンク不安定時の再選出を維持する。再起動選択時の理由・役割・各障害の開始時刻を保持し、NVS予約前、予約後、restart理由の保存後に現在のpolicyで再検証する。昇格・降格や実通信の回復で条件が変われば中止する。永続予約後の中止は安全側でbudget消費とcooldownを保持する。役割変更時は親切断の猶予と明示的な復旧要求の受付状態もリセットし、子Node時代の親再選択をRootのrouter再接続として流用しない。

`mesh_recovery/status`には`mqtt_progress_fresh`、`mqtt_probe_pending_id`、`mqtt_probe_ack_count`、`mqtt_probe_last_ack_ms`（32-bit uptime）、`restart_retry_ready`、`restart_persistence_blocked`を追加した。診断v2の`recovery_flags` bit 8は進捗確認ありを示す。正常時のSEN66 10秒計測、BLE中継、Mesh送信timeout 30秒、診断v2の保存・送信を維持する。PUBACKはブローカー到達の確認であり、Gateway collectorによる計測保存の確認ではない。Root自身のMQTT停滞・IP未取得だけでは再起動しない。子NodeのIP期限には`ip_timeout`（永続理由番号10、boot理由`mesh_ip_timeout`）を使用し、既存理由番号は変更しない。

障害予防・拡大抑制（同日のレビュー反映）:

- `mesh_tx_policy.c`は実際のMesh送信結果を短いRAM lockで記録する。NO_MEMORY/TIMEOUTごとに通常データの投入間隔を1→2→4→8→16→30秒へ広げ、失敗直後の投入を止める。最後の該当失敗から30秒以上かつ3回連続のMesh送信成功で通常へ戻す。単発の成功では制限を解除しない。制限中はNode全体の通常データを最大1件／間隔に抑える。
- 対象はSEN66・環境計測・BLEのMQTT投入。センサの10秒計測、既存BLEの10秒制限、DHCP/TCP ACK/その他Mesh制御パケットは変更しない。PUBACK監視は制限を受けず、30秒間隔・未確認1件を維持する。既存MQTT/TCP/Meshの再送は別に動くため、無線上の送信回数が30秒に1回になるわけではない。Mesh送信timeoutは30秒のまま。
- ESP-MQTT 1.1.0の`.outbox.limit`を12 KiBに設定する。通常データ・通常status・診断は8 KiBまでとし、監視・登録に4 KiBの余裕を残す。BLEの小さなburstと計測を収容しつつ滞留を有限化する初期値で、全heap占有量の上限ではない。新しいadapterではtopic・MQTT headerも含め、mutex取得と容量確認と投入を同じ所有区間で行う。
- 通常enqueue・outbox観測はmutexを待たない。拒否されたSEN66は既存の最新値mailboxへ戻すが、新しい値や登録変更を上書きしない。次の既存sender周期で再試行し、30秒超は破棄する。BLEは既存の失敗時頻度制限をそのまま使い、新しい再送queueを作らない。混雑時にBLEのその回の中継が見送られることはある。
- 既にoutboxに入ったデータの強制削除・順序変更は行わない。古いデータは既存30秒期限とMQTT所有の削除通知で処理する。MQTTタスク停止中は期限切れ処理も止まるが、その間にアプリ側の投入を増やさない。mutex busy時の`mqtt.outbox_bytes`は最後に取得できた値を表示する。
- 送信制限値は`src/communication_limits.h`、IP待ち時間は`src/mesh_recovery.h`の`#ifndef`付き定数で調整できる。MQTT adapterは診断をOFFにしても必要で、既存のhash検証済みbuild-local patchとして適用する。診断v2の形式・保存・5分間隔の送信を維持する。

ホストテストで接続フラグがtrueのままの停滞、即時ACK、無関係ACK、期限切れ、再接続、送信／event loop停滞、再boot後の再試行、安定復帰、NVS失敗、Root保護、timer wrapを検証する。今回の変更の実機RF試験は未実施。

fieldの定義と監視上の注意は[`docs/developer/data-and-mqtt.md`](../../../docs/developer/data-and-mqtt.md#mesh診断status)を参照する。`parent_disconnect_count`や`mqtt_disconnect_count`は障害回数ではなく、単独で異常判定に使わない。

### Gateway reboot後のDHCP/MQTT自動復旧

Gateway APが一時的に消失しても、root external STAはparent再接続後にDHCP、IPv4、MQTTを自動再確立する。parent disconnect時にはstatusの`ip`を`0.0.0.0`相当にclearするため、直前のGateway leaseを到達可能なIPとして扱わない。root/child役割、MAC、IPアドレスを運用設定や固定仕様にしてはいけない。

AtomS3 Lite 2台の試験では、Gateway reboot後にrootのAP再接続、DHCPでのIP取得、MQTT、SEN66、両NodeのBLE relayが自動復帰した。Node再起動、USB操作、手動再Provisioningは不要だった。設計理由と確認範囲は[ESP-WIFI-MESH decision](../../../docs/decisions/esp-wifi-mesh-node-networking.md#gateway-ap一時消失後の自動復旧)を参照する。

### 旧Provisioning BLEの実機確認とBlueZ cache（履歴）

旧BLE方式の試験では、AtomS3 LiteのProvisioning ServiceをRaspberry Pi Gateway / BlueZ 5.82から確認しました。以下は旧firmwareの記録であり、現在の未設定NodeはDiscovery BLEとUSB Provisioningを起動します。Node IDは例示用の値に置き換えています。

```text
Name: OMK_112233445566
Provisioning UUID: c2f08e31-75fd-4f81-9e6d-4f89a3bc1d27
```

当時の確認では、`bluetoothctl info`に過去のDiscovery UUIDのService Dataが残る場合がありました。現在送信中のBLEアドバタイズを確認するときは、`bluetoothctl info`のキャッシュだけで断定せず、次のようにraw LE Advertising Reportを確認します。

```bash
sudo btmon
```

## OMK networkと今後のNode

Raspberry PiはOMK専用APを提供し、NodeはそのWi-Fiへ接続します。既定SSIDは`OMK-XXXXXX`で、生成方法は[Gatewayネットワーク設計](../../../docs/developer/networking.md)を参照してください。任意のSSIDも使用でき、中央での識別子発行は不要です。初回登録はUSB Serial/JTAG、通信層はESP-WIFI-MESHを使用します。APのLAN client側をSORACOM外部通信へ転送する機能は対象外です。

電波が弱いフロアでは、`ble_scan` capabilityを持つAtomS3 LiteをBLE scanner / relayとして配置できます。BLE中継専用NodeにLogical IDの登録は不要です。Nodeは接続不要のSwitchBot BLEアドバタイズをactive scanで受信し、Wi-Fi/MQTTでGatewayへraw relayします。Gatewayが物理identityをregistryで解決する方式で実装済みです。詳細は上記relay節を参照してください。

## SEN66欠測の診断（2026-10-08、実機検証前）

この診断追加は読み取り周期、MQTT QoS、BLE scanning、Meshの選出・親切替・recovery、センサrecoveryを変更しない。対象は現在の1 Node / 1 SEN66 endpoint。登録statusのスキーマは変更せず、既存の30秒周期（および既存イベント時）の`omk/node/<node_id>/status`へ`sen66`オブジェクトを追加する。新しい定期メッセージや測定ごとのログは増やさない。最初の読み取り前は`sen66`を省略する。診断のバッファ確保・構築に失敗した場合も元のNode statusを送る。

### 既存経路から分かった原因候補

- `sensor_manager.c`は読み取り・publishの処理終了から10,000 ms後に次回を実行する。固定時刻の10秒周期ではなく、I²C処理、MQTT mutex待ち、タスクの実行待ちが加算される。正常時もProduct Name、data_ready、測定値の3回の応答待ち（各20 ms）があるため、30分180件は概算。これだけで129件を説明できるとは断定しない。
- `sen66_sensor.c`は毎回Product Nameを読み、未準備なら測定値を読まずに戻る。I²C送受信timeout（各100 ms）やCRCエラーは未送信となる。Product Name確認のI/O/CRC失敗も`ESP_ERR_INVALID_STATE`へ集約されるため、製品交換と同様に即時recoveryへ入る。通常の読み取りエラー3回連続、または60秒間測定成功なしでもrecoveryとなる。次の探索は60秒後、起動にはstop/reset等の待ち時間があり、初回読み取りは起動後さらに10秒後。30～48秒の全欠測がrecovery由来とは限らない。
- 成功してもLogical IDが取得できなければpublishしない。`mqtt_registration_publish_sen66()`はMQTT clientなし／未接続ならenqueueせず戻り、その測定をアプリケーションから再試行しない。topic/payload構築失敗、enqueueの負戻り値でも未送信になる。
- SEN66は`esp_mqtt_client_enqueue(..., qos=0, retain=0, store=true)`を使う。採用中のmanaged `espressif/mqtt` 1.1.0ではmutex取得後にoutboxへ格納し、別taskが送る。負値はメモリ不足等、outbox制限超過は-2となる。QoS 0は送信処理後にoutboxから削除され、BrokerのACKはない。期限切れ削除もあり、enqueue成功はBroker到着を保証しない。
- root自身は外部STA netifから送信する。子Nodeの通信はMesh/IP転送を使い、rootアプリケーションが子のSEN66 JSONを再publishする構成ではない。002やroot statusの到着だけでは001の読み取り・投入成功を証明できない。

### statusの診断項目

`sen66`以下の値は起動からの累計。センサのclose/restart/recoveryではリセットしない。

| 項目 | 意味 |
| --- | --- |
| `boot_id` | 最初の読み取り時に生成する64-bit乱数の16桁hex文字列。NVSへは書かない |
| `snapshot_ms` | 診断生成時の64-bit uptime（ms） |
| `read_attempts` / `read_success` | driver read呼び出し数 / ESP_OKかつdata_ready=trueの数 |
| `not_ready` / `read_errors` | ESP_OKかつdata_ready=false / readのエラー数。ready=true後の値取得失敗もerrorのみ |
| `last_read_error` | 最後のreadエラーのesp_err_t値。成功後も保持。製品名確認失敗の内訳は既存driverが集約するため区別できない |
| `publish_attempts` / `publish_failures` | SEN66 MQTT publish API呼び出し数 / エラーreturn数 |
| `publish_unavailable` | clientなし・未接続での失敗数。`publish_failures`の内数 |
| `enqueue_success` / `enqueue_failures` | SEN66測定の実enqueue APIで非負／負の戻り値となった数。未接続などenqueue前の失敗を含めない |
| `last_enqueue_seq` / `last_failed_seq` | 最後にenqueue成功した測定連番 / 最後にpublish APIが失敗した測定連番。Logical ID待ちを後者に含めない |
| `last_read_ms` | 最後の読み取り開始uptime（64-bit ms） |
| `last_interval_ms` / `max_interval_ms` | 読み取り開始から次の開始までの直近／起動後最大時間。recovery待ちも含む |
| `max_read_ms` / `max_publish_ms` | read / SEN66 publish API実行時間の起動後最大値（ms）。タスクの実行待ちやmutex待ちを含む |

探索・初期化の試行は`read_attempts`に含めない。既存registration statusの`connected_sensors`、`sen66_rc`、`sen66_to`と併せて確認する。カウンタは短いcritical sectionでsnapshotするが、読み取り／publish処理の途中にstatusが出ることはある。処理完了時は`read_attempts = read_success + not_ready + read_errors`、`publish_attempts = enqueue_success + publish_failures`となる。瞬間的な1件差を損失と判断せず、次のstatusでも確認する。`read_success - publish_attempts`が継続して増える場合はLogical ID待ちを疑う。

### 測定連番と欠番の判定

既存`omk/<logical_id>/sen66`の値と`uptime_ms`を維持し、次のオブジェクトを追加する。

```json
"meta": {"boot_id": "0123456789abcdef", "seq": 123, "enqueue_seq": 120}
```

`seq`は読み取り成功ごとに1から採番する。Logical ID待ちやMQTT失敗でも進む。`enqueue_seq`は投入成功の順番で、失敗した投入の番号は次回へ持ち越す。QoS 0で戻り値が0でも投入成功であり、MQTTのmessage_idを連番としては使わない。statusの`enqueue_success`と同じ系列。両方uint32で差はmodulo 2^32として扱う。MCU再起動ではカウンタは0へ、次の成功連番は1へ戻り、`boot_id`も新たに生成する（乱数のため一意性は確率的）。違う`boot_id`間の差を欠番と判定しない。センサrecoveryやMQTT再接続だけでは起動識別子・連番を変えない。

同じ`boot_id`の受信測定A、Bを連番順で比較し、`Δseq = B.seq - A.seq`、`Δenqueue = B.enqueue_seq - A.enqueue_seq`とする。重複は除外し、遅延・順序逆転が落ち着いてから集計する。

| 観測 | 判定基準 |
| --- | --- |
| 時間が空いたのに`Δseq=1` | 間に読み取り成功がない。`not_ready` / `read_errors` / 読み取り実行間隔を確認 |
| `Δseq > Δenqueue` | 差の件数がenqueue成功前に未送信。`publish_unavailable`、`enqueue_failures`、Logical ID待ちを切り分ける |
| `Δenqueue > 1` | `Δenqueue-1`件はenqueue成功後にこの観測点で受信できていない。例: seq 6/enqueue 2 → seq 8/enqueue 4なら1件 |
| `read_attempts`自体が増えない／開始間隔が大きい | センサ再探索待ち、read/publishでのブロック、タスク実行遅延を疑う。`last_read_ms`、各最大時間、既存recoveryカウンタで区別 |
| `read_errors`が増加 | I²C、CRC、identity確認等の読み取り側。`last_read_error`と既存ログを確認 |
| `not_ready`だけが増加 | 通信上は成功しているがセンサ未準備 |
| `enqueue_failures`が増加 | MQTT outbox投入APIが失敗 |
| `publish_unavailable`が増加 | enqueue前にMQTT clientなし／未接続と判定 |

最後の受信後の未着は、同じbootのstatusの`enqueue_success`と最後の受信`enqueue_seq`の差でも把握できるが、直近のoutbox滞留・statusと測定の処理時差を除いて評価する。投入後の未着だけでMQTT内部・IP/Mesh・Broker・購読側のどこかまでは確定できない。Gateway購読が途切れていないこととcollectorのraw記録を照合し、単なる遅延を除外する。QoSや周期を変更する判断はその後に行う。

collectorは追加オブジェクトもraw JSONへ保存する。generic catalogはオブジェクトを表示項目にせず、transformerとDashboardは従来の測定フィールドを読む。Parquetにはこのmetadataを追加しないため、診断にはMQTTの直接採取またはraw JSONLを使う。

### sen66-001での実機確認手順（今回未実施）

1. Gatewayで下記を開始し、更新前のstatus／測定を採取する。既存設定・配置・負荷を記録し、002は比較用にそのまま運用する。
2. `09dda0d5a8f2`の001だけをUSB接続し、対象Node IDとポートを確認して、通常更新の`scripts/flash-omk-node.sh atom-s3-lite <port>`を使う。これは今後実施する手順であり、この調査では実行しない。現在のsource buildには保護した試験中のMesh変更も含まれる。prebuilt packageを使った更新では未commitの診断コードは入らない。
3. 同じ配置へ戻して30～60分採取する。`is_root`、RSSI、MQTT disconnect、既存recoveryカウンタも並べ、30～48秒の欠測ごとに上記の差分を調べる。
4. 別区間で001を1回再起動し、`boot_id`の切替と連番1への復帰を確認する。センサrecoveryは連番をリセットしないことも、自然発生時の前後で確認する。

Gatewayホスト上のBrokerに対する採取例（任意の空き出力ファイルへ保存、Ctrl-Cで終了）:

```bash
mosquitto_sub -h 127.0.0.1 -F '%I %t %p' \
  -t 'omk/sen66-001/sen66' \
  -t 'omk/node/09dda0d5a8f2/status' \
  -t 'omk/node/09dda0d5a8f2/registration/status' \
  -t 'omk/sen66-002/sen66' | tee sen66-diagnostic-capture.log
```

## MQTT poll 100ms比較試験（2026-10-08、実機未適用）

読み取り／enqueue成功後の欠測に対する試験版。変更前の実測値は利用者の採取結果で、root `09dda0d5a8f2`の10分間のPUBLISHは592件（BLE raw 530、SEN66 50、Node status 8、Mesh recovery status 4）、間隔中央値1.014秒、591区間中563区間が0.8～1.2秒。SEN66 enqueue連番57件のうち未着7件（12.3%、別期間24.2%）。この作業では実機への適用・新たな実測は行っていない。

### ソースと設定の根拠

`dependencies.lock`はESP-IDF 6.0.1／`espressif/mqtt` 1.1.0。実際の`.pio/build/atom-s3-lite/compile_commands.json`も`managed_components/espressif__mqtt/mqtt_client.c`と生成された`config/sdkconfig.h`を参照し、CMakeの`SDKCONFIG`は追跡中の`sdkconfig.atom-s3-lite`を指す。別にインストールされた古いIDF内のMQTTソースは根拠にしない。

採用中のソースには次の経路がある（依存ソースは無編集）:

- `esp_mqtt_client_enqueue()` → `mqtt_client_enqueue_publish()` → `mqtt_enqueue()` → `outbox_enqueue()`。OMKの測定・BLE・診断はQoS 0、store=true。投入時刻・データを保持してQUEUEDへ入れる。enqueue自体はtransportのpollを起こさない。
- `esp_mqtt_task()`の各ループ先頭で期限切れを削除し、接続中は受信処理後、`outbox_dequeue(..., QUEUED, ...)`で1件だけ送信する。QoS 0は送信成功後に`outbox_delete_item()`で削除する。
- 末尾で`esp_transport_poll_read(..., max_poll_timeout(..., MQTT_POLL_READ_TIMEOUT_MS))`を呼ぶ。変更前の`mqtt_config.h`のfallbackは1,000ms。`max_poll_timeout()`はoutbox件数を見ておらず、event queue size=1では指定値をそのまま返す。queue size>1の短縮分岐もcustom event数に基づくものであり、今回は変更しない。
- Brokerから読めるデータがなければ毎回pollの待ち時間が加算され、enqueueされていても約1件/秒になり得る。常に1件/秒という制約ではなく、受信・transportエラー・task schedulingでも変わる。TCP ACKだけではMQTT socketのreadデータにはならない。
- `outbox_delete_single_expired()`は`current_tick - item->tick > 30000`を満たす項目を削除する。QoS 0の未送信項目も対象で、送信前のループ先頭で消え得る。投入成功・MQTT切断0でも欠落が起きる説明になる。

[ESP-MQTT Issue #326](https://github.com/espressif/esp-mqtt/issues/326)は同様の1秒間隔を報告している。OMKの原因確定は100ms版での実測待ち。BLEはアドレスごとの10秒間隔制限なので、複数機器の合計やburstは約1件/秒の排出能力を超え得る。今回BLE設定・中継判定は変更しない。

| 項目 | 変更前の実効値 | 試験版の実効値 |
| --- | --- | --- |
| `MQTT_USE_CUSTOM_CONFIG` | 無効 | 有効（Kconfigのpoll指定用） |
| `MQTT_POLL_READ_TIMEOUT_MS` | 1000 | **100** |
| `MQTT_REPORT_DELETED_MESSAGES` | 無効 | **有効**（診断用） |
| outbox保持期限 | 30000ms | 30000ms |
| MQTT buffer / task stack / priority | 1024B / 6144B / 5 | 同じ |
| event queue size / API mutex | 1 / 有効 | 同じ |
| TCP / SSL / WS / WSS default port | 1883 / 8883 / 80 / 443 | 同じ |
| keepalive / network timeout / reconnect | 120s / 10000ms / 10000ms | 同じ |
| protocol / transport / core affinity / external memory | 既存設定 | 同じ |

設定変更はAtomS3 Liteの`sdkconfig.atom-s3-lite`だけ。custom config有効化で表示される他の値は従来のfallbackと一致する。生成されたsdkconfig JSONの比較でもMQTT以外の実効設定差はない。QoS・SEN66周期・Wi-Fi・Mesh・provisioning・Gatewayは変更しない。100msは待ち時間であり、実際のPUBLISH間隔や10件/秒を保証する値ではない。

### 追加診断

既存の`omk/node/<node_id>/status`に`mqtt`オブジェクトを追加する。既存`sen66`オブジェクトと測定の`meta`連番は維持。SEN66を検出しないNodeでもMQTT診断は出せる。status周期・topic・retain・QoSは変えず、新しいtimerや高頻度メッセージ・削除ごとのログは追加しない。

| フィールド | 取得方法・限界 |
| --- | --- |
| `mqtt.outbox_bytes` | status生成直前にmutex待ちなしで取得した`esp_mqtt_client_get_outbox_size()`。busyなら直前の取得値。MQTT全topic合計の格納データbyte数。件数や全heap占有量ではなく、このstatus自身のenqueue前の値 |
| `mqtt.outbox_sampled_max_bytes` | 各既存enqueue呼び出し後とstatus生成時に同APIで観測した起動後最大値。非同期送信との競合で瞬間的なpeakを逃す可能性があり、厳密な最大値ではない。registrationのoutbox投入による瞬間peakも保証しない |
| `mqtt.expired_messages` | `MQTT_EVENT_DELETED`受信回数。QoS 0を含む全topic累計。送信成功後の通常削除を含まない。ライブラリのイベント配送自体が失敗した分は数えられない |
| `mqtt.error_events` | `MQTT_EVENT_ERROR`受信回数。接続・受信・TLS等も含むため、送信エラー専用カウンタとは解釈しない |

カウンタ／最大値はMCU再起動で0へ戻り、MQTT再接続では保持する。計数は短いcritical section、MQTT API呼び出しはその外で行う。SEN66のpublish時間には診断用のoutbox API読取り時間も含まれる。

MQTT 1.1.0の`mqtt_delete_expired_messages()`は削除関数の戻り値を`>= 0`で判定するため、QoS 0のmsg_id=0も最後まで通知される。イベントで有効なのはmsg_idのみで、topic・payload・QoS・SEN66連番は得られない。したがってSEN66だけの期限切れ件数、失われたseqとの1対1対応、厳密なqueue peak、送信だけのエラー数やBroker到着数は実装していない。これらのための依存ライブラリ改造は行わない。既存のSEN66欠番と全topicの期限切れ増加を同じ時刻区間で比較する。

### 比較試験と判定

1. 001だけを対象に、現在の配置・BLE機器数・Gateway負荷で変更前を採取する。既存のMQTT購読に加え、Gateway `wlan0`のroot IPからの1883/TCPを10分間以上captureする。保存先を分け、取得時間とNodeのuptime、root roleを記録する。
2. 対象が`09dda0d5a8f2`であることを確認して、将来の適用時に通常更新の`scripts/flash-omk-node.sh atom-s3-lite <port>`を使う。今回この操作は実行しない。このsource buildには保護した既存Mesh試験変更とSEN66診断が含まれる。002は更新しない。
3. 同じ場所・負荷に戻し、001がrootで安定してから同じ長さの区間を採取する。root交代・再起動・購読切断の区間は分ける。100msで追加の調整はせず、まず10分、その後30～60分の継続確認を行う。
4. TCP streamを再構成して**MQTT PUBLISHメッセージ単位**で集計する。TCP再送を重複計上せず、1 packet内の複数PUBLISHや分割されたPUBLISHを区別する。MQTT topicと送信元root IPで範囲を合わせる。

| 比較指標 | 確認する内容 |
| --- | --- |
| 全PUBLISH | 中央値・p95・0.8～1.2秒区間割合、10分の件数、topic別件数。変更前1.014秒／95.3%／592件との比較。低負荷なら間隔が長いだけで不良とはしない |
| SEN66 | 同一`boot_id`の`enqueue_seq`欠番数、受信率、`seq`・read/publish/enqueueカウンタ。変更前7/57=12.3%（別期間24.2%）から低下するか。採取端の未送信残りと遅延到着を区別 |
| BLE raw | 総件数に加えBLEアドレス別の到着率・空白時間。SEN66だけ改善してBLEが悪化していないか |
| outbox | `outbox_bytes`の増え続ける傾向、観測最大値、`expired_messages`の区間差。status自体もqueueを通るので受信時刻だけでなくuptimeを使う |
| MQTT | `mqtt_disconnect_count`・`mqtt_connected`・`mqtt.error_events`、再接続の有無 |
| メモリ | `free_heap_bytes`・`minimum_free_heap_bytes`。最小値は起動からの累計なので同程度の運転時間で比較 |
| Mesh | `is_root`・layer・RSSI・parent切替／切断、Mesh tx失敗、既存recovery診断。通信状態の変化とpoll効果を混同しない |

負荷が同程度の区間で1秒付近の排出制約が解消し、outbox滞留と期限切れ増加が収まり、SEN66欠番も減るなら仮説を支持する。期限切れが増え続けるなら排出能力／負荷を再調査する。期限切れ0でも欠番が残る場合は、別の投入後損失経路を疑い、QoS等を自動で変更しない。変更前firmwareにはoutbox診断がないため、変更前の期限切れ件数を今回の結果から逆算して確定しない。

ローカル検証はESP32テスト201件成功、AtomS3 Liteビルド成功、`git diff --check`成功。回帰テストは採用中outbox C実装による30秒境界とQoS 0の削除通知、診断のbyte数／観測最大値／イベント計数、SEN66連番とstatus容量、実効Kconfig／ビルド参照先を検証する。実際の無線送信間隔はローカルテストで模擬せず、実機captureで確認する。

## 通信停止の局所化（2026-10-22、実機未検証）

登録用publishとsubscribeは、既存のMQTT outboxへ待ち時間0で投入する。socket I/Oは既存MQTT taskだけが実行する。登録・再接続同期は投入できなかった段階から再試行し、NVS保存や成功済みの投入を繰り返さない。新しい登録要求は古い未完了要求を置き換え、retained config消去義務は保持する。QoS 1の制御パケットが期限切れになった場合も接続同期を再試行する。投入成功はBroker到達を意味しない。IP取得後のreconnectは固定版MQTTの既存のイベント要求APIを維持する。

TCP/IPのMesh送信callbackはEthernetフレームを固定4枠（各最大1560バイト）へコピーして戻る。単一の`mesh_netif_tx`タスク（stack 4096バイト）が`esp_mesh_send()`を直列に実行する。満杯なら即座に失敗を返し、無制限に蓄積しない。親接続変更前・役割不一致・投入後10秒以上の未送信フレームは破棄する。現在実行中のMesh APIは中断できない。単発待機は5秒に短縮し、Root broadcastは送信開始位置を巡回させて遅い子Nodeによる偏りを抑える。TCP再送等に回復を委ねるため、負荷時の個々のパケット配送は保証しない。

予算消費後の再起動再試行は、各bootの復旧判定開始から10分＋既存jitter（0～120秒）以降。15分の正常継続での予算解除、NVS保存・照合、役割再検証、接続中Rootの保護を維持する。故障継続中は10～12分ごとの再起動になり得るが、起動直後の連続再起動は許可しない。計測周期、ペイロード仕様、PUBACK監視、送信バックオフ、outbox容量、既存診断機能は維持する。

## Build

```bash
cd firmware/esp32/omk-node
pio run -e atom-s3-lite
```

AtomS3 Liteのbuild、upload、GatewayによるDiscovery v1検出を確認済みです。

## Repository hygiene

- 追跡する: `src/idf_component.yml`、`dependencies.lock`、`sdkconfig.defaults`、 `partitions.csv`、firmware source
- 追跡しない: `.pio/`、`managed_components/`、provisioning credential

managed componentはmanifestとlockから再現します。`platformio.ini`が参照する`sdkconfig.defaults`と`sdkconfig.atom-s3-lite`は必要な入力設定として追跡します。

## Troubleshooting

- **`intelhex` is missing**: pinned PlatformIO/ESP-IDF package setを整える。
- **旧Provisioning関連のheader missing**: 現在のUSB方式は`network_provisioning`を使用しません。旧build設定や旧sourceが混在していないか確認してください。
- **legacy GAP symbolのlink error**: Bluedroid BLE-onlyとBLE 4.2 legacyのKconfigを有効化する。BLE 5 feature設定ではraw legacy advertisingと整合しない。
- **GCC 15の`old-style-declaration`が-Werrorになる**: `CONFIG_COMPILER_DISABLE_GCC15_WARNINGS=y`を維持し、ESP-IDF sourceを編集しない。
- **`No space left on device` under `/tmp`**: エラーに表示された一時ディレクトリと、そのファイルシステムの空き容量を確認する。

## Gateway用prebuilt package

配布バイナリの第三者許諾は[Third-party notices](prebuilt/atom-s3-lite/THIRD_PARTY_NOTICES.md)を参照し、バイナリの再配布時は同文書を同梱してください。[監査記録と更新手順](LICENSE_AUDIT.md)も参照してください。

一般利用者は[「3-2. SEN66 Nodeをセットアップする」](../../../docs/user/esp32-node-setup.md)に従って設定します。開発者はsource commit後のclean HEADから`./scripts/build-omk-node-package.sh`を実行し、scriptはPlatformIO clean→buildを実行し、3 binaryとmanifestを更新します。SBOMと監査記録は自動更新しないため、同じbuild成果から再生成・照合してpackageと一緒に別commitへ含めます。第三者通知は依存・リンク構成の変化に応じて確認します。Gateway上ではbuildしません。詳細は[package更新とUSB setup設計](../../../docs/decisions/usb-node-setup.md)を参照してください。PC用flash scriptは開発・復旧用として維持します。

## Mesh/MQTT停止原因の診断計測版（2026-10-10、実機未適用）

SEN66の有無に依存しない共通の診断機能。診断追加時はMesh方式・送信timeout・復旧条件を維持した。現在の通信処理は上記「通信停止の局所化」のとおりで、診断の記録・保存条件は変更していない。診断はCMakeの`OMK_NETWORK_DIAGNOSTICS`で有効化する（この試験ソースでは既定ON）。既存の未commit試験変更を含むため、HEAD名だけではバイナリを識別できない。

### 観測範囲と保存

- `mesh_netif.c`から呼ぶ**アプリ側の`esp_mesh_send()`**を測る。`libmesh`内部で処理される子Nodeの中継回数・中継時間は測らない。
- MQTT 1.1.0のenqueue/outbox API入口・mutex取得前後・return、および`esp_mqtt_write()`を計測。mutex取得所要時間にはスケジューリングと計測フックの時間を含む。全APIのmutex所有者を追う機能ではない。
- 呼び出しごとに時刻と固定長RAMだけを更新する。ログ、malloc、NVS、MQTT、USBを計測ロック内で呼ばない。同時実行スロットは8。超過は`dropped`として明示し、待たせない。取得後時間はmutex取得からreturn直前までの経過時間で、CPU実行時間ではない。
- 既存30秒イベントの投稿・処理開始／終了、復旧判断完了を記録する。投稿時刻が更新され続けても処理完了が止まっていれば検出する。イベントpayloadは従来どおり空で、診断のための動的確保を増やさない。個々のイベントのqueue滞在時間は測らない。
- 専用のpriority 1、静的4KiB stackのobserverが約1秒ごとにRAMを読む。MQTT、Mesh、デフォルトイベントループには依存しない。critical sectionは最大1KiBのコピー以下で、CRC・整形・I/Oは外で行う。
- RTC NOINITは通常進捗用2スロットと異常保持用2スロットに分離する（CRC・形式v2・世代付き）。異常保持側は正常snapshotで上書きせず、soft reset時に無効化しない。各側の書込先を交互に使い、中断更新では以前の有効スロットへ戻る。power-on/brownout時だけRTCを信用せず、NVSの有効記録を使用する。RTC確保分は通常heapとして利用できる容量を減らす。
- 10秒超のAPI呼び出しを早期検出、30秒以上（実行中／完了とも）を重大として`critical`で区別する。処理未完了だけでは重大にせず、30秒経過時に重大へ昇格する。90秒超のタイマー／イベント処理／復旧判断停滞も重大。10秒はMeshの30秒timeout到達を意味せず、復旧判定には影響しない。observerだけがNVSの`omk_netdiag/snapshot1`へ保存する。正常時の定期保存はしない。
- **同じ種別・同じ重大度の再保存はしない**。早期→重大への昇格は追加保存対象。保存済み種別／重大種別と成功回数を同じblobに保持し、再起動・電源断で予算を再付与しない。初回以外は10分以上空け、最大16回。早期だけの保存は保存回数4未満の間に限定し、重大異常用の予算を残す。soft reset後も保留異常を保持し、保守的に再び10分待って保存する。再武装・消去コマンドは設けない。
- 保留領域は優先度付きの固定1 snapshot。保存待ち中に異常が解消しても保持し、重大が早期より優先する。同じ重大度では未保存の先行記録を優先し、同じbootの異常種別をすべて含むsnapshotなら更新する。新しい軽微な異常が既存の重大記録（NVSを含む）を置換することはない。有限容量のため全異常履歴を保証せず、同時期の別異常や重大記録より軽い異常を省略し得る。
- 既存24KiB NVSを共有するため、保存前に必要量＋128 entriesの空きを要求する。不足・不正な保存内容・保存失敗は保存を停止し、既存NVSを消去しない。失敗後は同一bootで再試行せず、RTCに残った保存エラーも次のsoft resetへ引き継ぐ。
- flash操作は通信の呼び出し元では実行しないが、cache/CPUへの遅延は発生し得る。`flash_us`で保存区間を区別する。NVS/MQTTライブラリ内部の動的確保は残る。電源断で失われるのは最後のcommit以降の情報であり、電源断直前の全記録を保証しない。中断・失敗したflash操作の物理的な書込回数を永続予算で完全に数えるものではない。

### Gatewayでの回収

既存の`sen66_tx`タスク（SEN66なしでもMQTT登録開始時に起動）が、`omk/node/<node_id>/network_diagnostics`へ完全なJSONを非retain、QoS 0、store=trueで送る。現在bootの統計（view=0）から開始し、RTC/RAM側の保持記録（view=1）、最後のNVS記録（view=2）の順で循環する。既存の5分周期の枠を使い、1枠に1件だけ送る。保存記録のない枠はview=0に置き換える。全viewが存在し投入成功が続く場合、各viewは15分間隔となる。保存記録がない正常時にも現在値を送るため、従来の送信ゼロからは増えるが、送信周期・3072バイトのJSONバッファ上限は増やさない。通常のNode statusは増量しない。診断専用`esp_mqtt_client_enqueue_diagnostic()`はMQTT mutexを待ち時間0で取得し、取得不能なら見送る。取得後はrecursive lock下で既存enqueueへ渡すため、そのmutexを再び待たない。投入失敗は同じ巡回位置で30秒後以降に再試行し、投入成功後だけ次の枠へ進めて5分空ける。view=0の再試行は現在値を再採取する。記録はどちらの場合も削除しない。既存enqueue内の短いメモリ確保・コピーとFlash/cache停止の影響は残るが、診断経路でsocket I/OやMQTT mutexの待機は行わない。observerとUSBは送信結果を待たない。

collectorの既定購読`omk/#`と既存の日別JSONLがそのまま保存する。専用ACK、分割転送、Gatewayの新しい保存基盤はない。enqueue成功を到着とはみなさず、Node側の記録を送信後も削除しない。欠落・重複はあり得る。Node・`view`・`boot`・`seq`・`at_us`の組で解析側が識別する。MQTT view=0の`seq`はbootごとに1から始まる採取連番で、失敗時の再採取でも進むため、欠番は配送損失数を意味しない。observerの連番やRTC/NVSには書き戻さず、view=1/2とUSBの既存連番は変更しない。topicから物理Nodeを識別し、JSONLの`received_at`とpayloadのboot内`at_us`を分けて扱う。エラー増分は同じNode・view=0・同じboot内で比較する。15分未満で再起動した障害やQoS 0の配送失敗では、直前の現在値を回収できない場合がある。

| 項目 | 内容 |
| --- | --- |
| `elf` | 記録したbootの`app_elf_sha256`。現在動作中のfirmware確認はMQTT view=0、またはUSB view=0/page=0 |
| `reasons` | bit 0～7:下記operationが10秒超、bit 8:timer停止、9:event完了停止、10:復旧判断停止 |
| `critical`, `saved_critical` | 同じbit配置の重大異常と、NVS保存済みの重大種別。`reasons`／`saved_reasons`の部分集合 |
| `saved_reasons`, `saves`, `save_error` | このsnapshot時点の保存済み種別、成功回数、最後の保存エラー |
| `stats` | 各行 `[op,calls,completed,errors,last_rc,last_ms,max_ms,last_wait_ms,max_wait_ms,last_locked_ms,last_end_us]` |
| `active` | 各行 `[token,task,op,phase,started_us,wait_us,acquired_us]`。時刻は同じboot内のみ比較する |
| `phase` | 1=入口、2=mutex取得待ち、3=取得済み。待ち中なら`at_us-wait_us`が観測時の経過時間 |
| `mesh_errors` | Mesh送信エラーの`[rc,count]`、最大8種類。超過は`mesh_error_other` |
| `progress` | `[ticks,posted,post_errors,entered,exited,tick_us,post_us,enter_us,exit_us,recovery_us,flags,stage,action]` |
| `flags` | bit 0=started、1=root、2=parent、3=rootless、4=IP、5=MQTT started、6=MQTT connected、7=restart budget spent |
| `flash_us` | observerによる直近flash保存の開始／終了。0の終了は未完了。NVS記録は自身のcommit完了時刻を含められない |

operation番号: 0=Mesh上り、1=Root下り、2=Root broadcastの各宛先、3=MQTT enqueue、4=outbox size、5=MQTT write、6=status handler、7=復旧判定＋action。Mesh送信成功はBroker到着を意味しない。CPU全体停止やobserver自身の停止後に、新しい情報を保存する保証はない。

### USBは補助回収（常設PC不要）

Linuxのnative USB Serial/JTAGで、設定変更を含まないASCIIコマンド`OMKDIAG <view> <page>\n`を受け付ける。view=0は現在値、1は保持記録、2はNVSキャッシュ。page=0は識別と重大度mask、1～8はoperation別stats、9～16は実行中スロット、17はイベント進捗、18は保存状態、19～26はMeshエラー種類。各pageは独立した観測であり全pageの同時snapshotではない。未保存viewは識別値が0。

応答は240バイト未満でUSB待ち時間0。TX満杯なら捨て、page 18の`usb_dropped`相当値で確認する。応答処理はMQTT/Mesh/NVSを呼ばず、再起動を要求しない。スクリプトはDTR/RTS操作やesptoolを使わず、HUPCLを解除してcloseによる切断動作を避ける。UART変換器の自動reset回路での利用は対象外。USBの接続で給電を切り替えないこと。

```sh
python3 firmware/esp32/omk-node/scripts/capture_network_diagnostics.py \
  --port /dev/serial/by-id/<native-usb-device> --node 9490b46aee0d \
  --output /tmp/omk-network-diagnostics.jsonl --once
```

`--once`を外すと補助的な継続採取（既定5秒）になる。Node ID不一致、切断、応答timeoutで停止し、再接続・resetは行わない。通常運用の回収はGateway JSONLを使用する。

### パッチ適用と検証

managed `espressif/mqtt` 1.1.0本体は編集しない。`patches/mqtt-1.1.0-diagnostics.patch`と元／適用後SHA-256を使い、ビルドディレクトリ内のコピーを生成する。CMakeは必ず生成コピーをコンパイルし、PlatformIOのpre scriptはCMakeを省略する増分buildでも検証する。依存ソースが変わればbuildを失敗させる。再生成可能な入力としてpatch、hash JSON、scripts、CMake設定を一緒に保持する。

ホストテストは実際のrecorder/observerを使用し、並行記録、長時間待ち、RTCの中断更新、NVS破損・容量不足・書込失敗・予算、同じ異常の抑制、USB満杯とJSON上限を検証する。実機では、3台のELF識別と既存通信負荷を揃え、Mesh→MQTT write→mutex待ち→status/復旧停止の時間的な重なりを確認する必要がある。今回は実機書き込みをしない。

診断保存形式はv2。未適用の旧診断版v1のblobが存在した場合は、自動消去や予算の初期化をせず保存停止とする。センサなしNodeのホスト統合テストでは、実際のrecorder/store・MQTT登録・senderを組み合わせ、別スレッドによるMQTT mutexの20分相当の占有中も診断の計測／保存が進み、解放後に既存topicへ送信できることを確認する。これは無線・実機の時間保証ではない。
