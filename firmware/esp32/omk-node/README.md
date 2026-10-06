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

topologyはrootを頂点とするtreeで、childはparent経由でrootへ到達する。parentまたはrootの喪失時にはMeshが自動再構成する。参加Nodeの増加やroot uplinkの不安定化に対しては、条件付きで標準APIによるroot再選出を要求する。[現行の再選出policyとlivenessとの関係](../../../docs/decisions/esp-wifi-mesh-node-networking.md#現行のroot再選出とlivenessの関係)を参照する。起動時は`esp_wifi_start()`、`esp_mesh_init()`、`esp_mesh_start()`の成功後にだけinternal-netif receive taskを開始する。Mesh初期化前に`esp_mesh_recv()`を呼ばない。

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

Mesh通信が成立しているのにMQTTだけが復旧しない場合は、Mesh起動済み、parent接続済み、rootlessでない、有効IPあり、MQTT client開始済み、MQTT未接続の状態が180秒連続したときだけsoftware restartする。Mesh再構成中や通常の通信断では発動しない。statusの`mqtt_connected`、`mqtt_disconnected_duration_s`、Mesh RX/TX counter、`last_omk_restart_reason`、`mesh_mqtt_liveness_restart_count`で診断できる。

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
