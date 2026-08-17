# OMK ESP32 Node

OMK NodeはAtomS3 Lite、オリジナルM5StickC、および将来のESP32-C3/S3
センサノードで共用するファームウェア基盤です。ボード固有のLCD、LED、ボタンは
この基盤に含めません。BLE中継ノード（`ble_scan`）と将来のSEN66ノードも同じ
基盤を使います。

この文書では、実装済みかつ実機で確認済みのDiscovery、USB Serial/JTAG
Provisioning、通常起動時のWi-Fi再接続を記録します。USB ProvisioningはAtomS3 Liteで
MQTT registration statusまでE2E確認済みです。MQTTによるBLE中継は
SwitchBot Meter一台の初期E2E実装だけを確認済みです。Gateway/Dashboardからの
汎用登録自動化、logical IDの割当は未実装です。

## 対応環境

- PlatformIO: `espressif32@7.0.1`（ESP-IDF 6.0.1）に固定
- フレームワーク: ESP-IDF
- Bluetooth: Bluedroid、BLE-only controller、BLE 4.2 legacy advertising
- 対応確認済みボード: AtomS3 Lite、オリジナルM5StickC

BLE 4.2 legacyを選ぶのは、Discovery v1が31-byte legacy advertisingと
Bluedroidのraw GAP APIを使うためです。NimBLEまたはextended advertisingへ
切り替える場合はプロトコル改訂が必要です。ESP32-S3のAtomS3 Liteでは8 MB、
オリジナルM5StickCでは4 MBのFlash設定をボード別に選びます。

`CONFIG_COMPILER_DISABLE_GCC15_WARNINGS=y`はESP-IDF 6のGCC 15互換設定です。
ESP-IDF本体を修正したり、広範な`-Wno-error`を追加したりしません。

## USB Serial/JTAG Provisioning（正式方式）

AtomS3 Liteの通常firmwareはUSB Serial/JTAGをprimary consoleとして使用する。
この同じUSB接続で、通常起動中にversioned JSON Lines protocolを処理する。
未設定Wi-Fi時にもTemporary SoftAPを開始せずtransportは常時起動するため、物理
ボタン、USB抜き差し、Gatewayの`wlan0`切替は不要である。

Gatewayでは次を実行する。SSID/PSK/Node IDはいずれも入力しない。CLIは
NetworkManagerの`omk-ap` profileからWi-Fi credentialを読み、`/dev/serial/by-id/*`、
`/dev/ttyACM*`、`/dev/ttyUSB*`を列挙して`identify`応答でNodeを選別する。

```bash
python3 scripts/provision_omk_node_via_usb.py
```

`--device`は調査時だけの明示指定であり、通常運用で固定device番号には依存しない。
Nodeは`set_wifi`受信後、既存の`wifi_station_save_credentials()`でFlash保存と
read-back照合を行い、`accepted`応答を返してからsoftware rebootする。credentialや
passwordはログ・応答へ含めない。再起動後は既存のSTA/MQTT registration起動経路を
そのまま使用する。

実機ログが同じstreamへ流れるため、CLIのper-command timeout既定値は10秒とする。実機判定は、CLIの`accepted`後にMQTT topic
`omk/node/<node_id>/registration/status`で`provisioned`または`registered`を確認して
行う。これは実機未接続のリポジトリ上では未実施である。

## Node ID

`node_id`のcanonical sourceはfactory eFuseのbase MACです。ファームウェアは
`esp_efuse_mac_get_default()`で6 bytesを取得し、順に64-bit FNV-1a
（offset basis `14695981039346656037`、prime `1099511628211`）を計算して、
下位48 bitsを12桁の小文字16進数として使います。Wi-Fi/BLEの初期化状態や
MAC種別に依存しないため、書込みPCと実行中Nodeで同じIDになります。

AtomS3 Lite実機ではbase / Wi-Fi STA MACが`ac:a7:04:03:d7:f8`、Bluetooth
MACが`ac:a7:04:03:d7:fa`であり、canonical node IDは`9af9509eb8b6`です。
Bluetooth MACを入力にしてはいけません。`5e08fee0631d`は現行canonical
algorithmと一致しない旧credential IDです。生成時の原因は未確定ですが、secretを
変えず`9af9509eb8b6`へ移行済みです。

## Factory secretとPoP

Security 1のPoPはNodeごとに生成する256-bit random secretです。firmwareに
共通secretを埋め込まず、BLE advertising、通常ログ、Wi-Fi設定、MQTT設定にも
含めません。

初回factory flashでは、custom `factory_secret` partitionに`OMKP`の4 bytesと
raw 32-byte secretを置きます。最初の起動でファームウェアは以下を行います。

1. `factory_secret`を検証して読み取る。
2. NVS namespace `omk`、key `prov_pop`へ32-byte blobとして保存する。
3. `nvs_commit()`後にNVSから読み戻して一致を確認する。
4. 成功確認後だけfactory partitionを消去する。

この順序により電源断が途中で発生しても、factory recordまたはNVSの少なくとも
一方から再実行でき、secretを失いません。通常のfirmware updateはNVSのsecretを
保持します。secret rotation / 再factory provisionは将来の明示的な操作として
扱います。

PC側の対応情報は`data/provisioning/nodes/<node_id>.json`に保存します。これは
mode `0600`、Git ignore対象であり、secretを標準出力やリポジトリへ出しません。
Security 1 APIはNUL終端文字列のPoPを要求するため、NVSに保持する32-byte raw
secretは実行時だけ64文字のlowercase hex文字列へ変換して渡します。

## Factory flashとpartition table

`scripts/flash-omk-node.sh`はeFuse base MACを読取り、同じnode IDを算出し、
Node固有secretとPC側credentialを作成してからfactory partitionとfirmwareを
書き込みます。通常の`pio run -t upload`はsecretを再生成しません。

partition layoutは次のとおりです。

| Partition | Type/subtype | Offset | Size |
| --- | --- | ---: | ---: |
| `nvs` | data / nvs | `0x9000` | `0x6000` |
| `factory_secret` | data / `0x40` | `0xf000` | `0x1000` |
| `factory` | app / factory | `0x10000` | `0x200000` |

`platformio.ini`の`board_build.partitions = partitions.csv`とESP-IDF側のcustom
partition設定の両方が必要です。PlatformIOのデフォルトsingle-app tableでは
`0xf000`は`phy_init`であり、custom tableが実機へ書かれていない状態でそこへ
secretを書き込むことは危険です。書込み前に生成物・実機双方のpartition tableを
確認します。

### 開発時だけの登録状態reset

Dashboard登録フローを再試験する場合だけ、次を明示的に実行します。

```bash
./scripts/reset-omk-node-registration.sh atom-s3-lite /dev/ttyACM0 --confirm
```

このスクリプトは一時的な開発用imageをflashし、NVS namespace `omk`の
`registered`と`logical_id`だけをeraseしてcommitします。両keyがread-backで
存在しないことを確認した後、通常firmwareを自動的に戻します。NVS partitionの
全消去、Wi-Fi credentials、`prov_pop`、`factory_secret` partitionへの操作はしません。
通常firmwareにはこの操作へ到達するruntime経路はありません。

### 開発時だけのWi-Fi Provisioning reset

Gateway APIによる初回Wi-Fi Provisioningを再試験する場合だけ、次を明示的に実行します。

```bash
./scripts/reset-omk-node-wifi-provisioning.sh atom-s3-lite /dev/ttyACM0 --confirm
```

一時imageは、最初に`omk/prov_pop`を32 bytesでread-backし、ESP-IDF Wi-Fi APIで
Flash保存のSTA configを空にしてread-backします。続いて既存helperで`registered`と
`logical_id`だけを消去し、最後に`omk/prov_pop`を再度read-backします。NVS partition全消去、
factory secret、factory credential、PoPへの書込みは行いません。完了後は通常firmwareを自動で
戻すため、次bootはWi-Fiへ接続せず、直接Provisioning BLEへ入ります。

### 開発時だけのWi-Fi credential投入

Wi-Fi Provisioningを介さずBLE relayなどを検証する場合だけ、次を実行します。

```bash
./scripts/set-omk-node-wifi.sh atom-s3-lite /dev/ttyACM0
```

SSIDは通常入力、PSKは非表示入力です。どちらもrepository、build flag、ログ、通常の
firmware sourceには保存しません。一時imageだけがESP-IDFの`esp_wifi_set_config()`を
使ってFlash保存のSTA configurationを書込み、read-backで一致を検証します。さらに
書込み前後で`omk/prov_pop`が32 bytesのままであることを確認し、`registered`、
`logical_id`、factory secret、Node IDは変更しません。スクリプトは直後に通常firmwareを
書き戻し、credentialを含む一時headerと専用build directoryを削除します。

これは開発専用の明示的なone-shot操作であり、production firmwareの通常boot、BLE
Provisioning、MQTT APIから到達する経路はありません。

## Discovery BLE v1

保存済みWi-Fi credentialを持つ通常bootのNodeは、次のService UUIDのraw legacy
advertisingを出します。Wi-Fi STAおよびMQTT registrationと並行して動作し、Gatewayが
Nodeを発見・状態確認する用途に使います。Wi-Fi未設定NodeはこのDiscoveryを開始せず、
後述のEspressif Provisioning BLEだけをadvertiseします。

- Service UUID: `7d2a4d90-7b64-4e3a-9f37-95e77d7b5101`
- Service Data: 厳密に10 bytes
  `protocol_version(1) | provisioning_state(1) | capabilities(2, big-endian) | node_id(6)`
- Protocol version: `1`
- Provisioning state: `0` = unregistered、`1` = provisioned、`2` = registered。
  保存済みWi-Fi credentialと`omk/registered`から決定する。
- Capability bits: `ble_scan = 0x0001`、`sen66 = 0x0002`。複数bitの組合せを許容する

Service Dataには`site_uuid`、SSID、Wi-Fi password、PoP、MQTT credential、
SORACOM情報その他の秘密情報を載せません。AtomS3 Liteのadvertising v1はGateway
管理画面で未登録OMK Nodeとして実機検出済みです。

接続後の開始要求用Control GATT serviceはadvertising payloadへ追加せず、service
discoveryで見つけます。これはWi-Fi設定済みNodeの再Provisioning用に残している移行中の
互換経路であり、初回Wi-Fi Provisioningには使用しません。

- Control Service UUID: `c1347091-4268-2fb1-884a-7d019a432154`
- START characteristic UUID: `c1347091-4268-2fb1-884a-7d019a432155`
- Property: write only。payloadは1 byteの`0x01`だけを受理する

不正長、値、prepared writeはGATT errorとして応答します。GATT callbackは検証、
応答、固定長4のFreeRTOS event queueへの投入だけを担当し、NVS操作、再起動、BLE
停止、Provisioning開始は行いません。`node_state_task`だけがeventを解釈します。

## SwitchBot Meter BLE relay（初期E2E）

通常bootのNodeはOMK Discovery advertisingと並行してpassive scanを行います。
最初の実機E2Eとして、SwitchBot Company ID `0x0969`のうち物理MAC
`cf:39:41:c7:ed:79`だけを対象に、既存Gateway decoderと同じMeter manufacturer
layoutを検証・復号します。これは汎用SwitchBot relayではありません。

対象広告を受信すると、最短10秒間隔で次をpublishします。

```text
omk/switchbot-meter-001/environment
```

```json
{"device_id":"switchbot-meter-001","quality":"normal","temperature_c":25.0,"relative_humidity_percent":49}
```

QoSは0、retainはfalseです。ESP側は時刻同期を行わないため`measured_at`は含めず、
Gatewayの`sensor-collector`が受信時刻を付けてJSONLへ保存します。将来はDashboard
登録情報を用いて、固定MACと固定`sensor_id`を置き換える予定です。

## 旧BLE Provisioning（廃止済み・履歴）

> この節と次節は旧方式の履歴であり、現行手順ではない。BLE Security 1、Control GATT、
> SoftAPを使った初回登録は削除済みで、初回登録はUSB Serial/JTAGのみで行う。

ESP-IDF 6ではBluetooth controllerの同一boot内でのdeinit後再initを前提にしません。
そのためDiscovery BLEとnetwork provisioning BLEを同時に動かしたり、同一boot内で
所有権移譲したりしません。起動時のfactory secretから`omk/prov_pop`への移送後、保存済み
Wi-Fi STA credentialで次のように分岐します。

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

`wifi_station_prepare()`はnetif、default event loop、default STA netif、Wi-Fi driverを
初期化してから、Flashに保存されたSTA credentialの有無を確認します。
`wifi_station_start_prepared()`はcredentialがある場合だけevent handler登録、STA開始、接続を
担当します。未設定Nodeはprepare済みのnetwork/Wi-Fi初期化をそのままProvisioning側で使うため、
`esp_netif`、event loop、Wi-Fi driverを二重に初期化しません。

従来のControl GATT `0x01`は、Wi-Fi設定済みNodeを明示的に再Provisioningする互換経路として
残っています。PoPが有効なら`omk/next_boot_mode`へ`PROVISIONING`を保存・commitして再起動し、
次bootの先頭でflagを消去・commitします。これはクラッシュ、watchdog、電源断後の
Provisioning boot loopを防ぐためです。このControl Service、START characteristic、boot flag、
Gateway側Control retryは将来のStep 4で整理・削除予定です。

Discoveryを使う通常bootでのClassic BT memory releaseはboot中に一度だけ行い、BLE-onlyとWi-Fi
STAの併用には影響しません。

## 旧Security 1 BLE Provisioning（廃止済み・履歴）

Wi-Fi未設定bootのProvisioningは`network_prov_scheme_ble`と`NETWORK_PROV_SECURITY_1`のみを
使用します。Security 0 / Security 2へfallbackしません。`CONFIG_ESP_PROTOCOMM_SUPPORT_SECURITY_VERSION_1=y`
が必要です。

- Provisioning Service UUID: `c2f08e31-75fd-4f81-9e6d-4f89a3bc1d27`
- Service name: `OMK_<12 hex node_id>`（例: `OMK_9af9509eb8b6`）
- PoP: 前節のNode固有secretをlowercase hexへ変換したもの

Provisioning BLEのservice nameは`OMK_<node_id>`です。`node_id`は12桁lowercase hexの
安定した内部識別子ですが認証情報ではありません。最終的なNode所有確認は、Security 1と
Node固有PoPによるhandshakeで行います。Gateway側はfactory flash時に作成したNode credential
storeを使ってPoPを取得します。PoP、SSID、Wi-Fi passwordはadvertising、通常ログ、MQTT、APIへ
載せません。

`NETWORK_PROV_WIFI_CRED_SUCCESS`でWi-Fi接続成功を記録し、`NETWORK_PROV_END`でProvisioning
managerをdeinitします。その後、event callbackから直接ではなくFreeRTOS task経由で
`esp_restart()`します。再起動後は保存済みcredentialを検出するため、Wi-Fi STA、MQTT registration、
OMK Discovery BLEの通常bootへ進みます。

Control GATT STARTは既存Wi-Fi credentialを消去せずに再Provisioningを明示する互換操作です。
この経路でもSecurity 1 BLE serviceを開始します。

AtomS3 LiteではPC側credentialのPoPを用いたSecurity 1 session、SSID/passwordの
保存、直後のWi-Fi接続を実機で確認済みです。SetConfigおよびApplyConfigはいずれも
成功しました。

### Windowsでの手動確認

WSLには通常BLE adapterが渡らないため、Security 1 clientはWindows側のPython 3.13
環境で実行します。Espressif公式`esp_prov`コードと`bleak`、`protobuf`、
`cryptography`を使います。service nameが取得できない場合もBluetooth MACで対象を識別して
確認できます。Wi-Fi未設定Nodeは起動直後からProvisioning BLEをadvertiseするため、Control
characteristicへの事前writeは不要です。

## 通常bootのWi-Fi STA

`wifi_station_prepare()`は保存済みcredentialの有無を確認し、必要なnetif/event loop/Wi-Fi
初期化を一度だけ行います。設定済みの場合だけ`wifi_station_start_prepared()`がSTA接続を開始します。
切断時は通常運用の再接続を行い、`IP_EVENT_STA_GOT_IP`で成功を記録します。SSID/passwordは
ログしません。Wi-Fi初期化が失敗した場合は、Provisioning/STAを開始せず、既存の安全側エラー処理に
従い、Discovery BLEは可能な範囲で継続します。

AtomS3 Liteでは再起動後およそ15秒でRaspberry Pi APへ再接続することを確認済みです。
AP側はSTA MAC `ac:a7:04:03:d7:f8`を観測し、`192.168.50.175`はreachable、pingは
4/4応答・packet loss 0%でした。Wi-Fi未設定bootではOMK Discovery BLEを開始せず、BLEは
network provisioningが単独で所有します。

### Provisioning BLEの実機確認とBlueZ cache

AtomS3 Lite実機（Node ID `9af9509eb8b6`、BLE address `AC:A7:04:03:D7:FA`）で、Raspberry Pi
Gateway / BlueZ 5.82から次を確認済みです。

```text
Name: OMK_9af9509eb8b6
Provisioning UUID: c2f08e31-75fd-4f81-9e6d-4f89a3bc1d27
```

これにより、Wi-Fi未設定Nodeが起動直後からProvisioning BLEへ入るStep 1を実機確認しました。
確認時に`bluetoothctl info`が旧Discovery UUID
`7d2a4d90-7b64-4e3a-9f37-95e77d7b5101`のService Dataを表示することがあります。未設定bootは
`discovery_ble_start()`へ到達せず、Provisioning実装もこのService Dataを設定しないため、同じ
BLE addressに対するBlueZの過去属性が残っている可能性が高いです。現在送信中のadvertisingを
厳密に確認するときは、`bluetoothctl info`だけで断定せず、次のようにraw LE Advertising Reportを
確認します。

```bash
sudo btmon
```

## OMK networkと今後のNode

Raspberry PiはOMK専用APを提供し、NodeはそのWi-Fiへ接続します。SSIDは将来
`omk-`とsite UUIDの短縮値で構成する案がありますが、OSSは任意の識別子・非中央管理
APでも使える設計を維持します。ランダムなWi-Fi passwordは公開識別子ではありません。
初回登録はUSB Serial/JTAGを維持し、将来のESP-Mesh-Lite等のnetwork backend変更と
独立させます。APのLAN client側をSORACOM外部通信へ転送する機能は対象外です。

電波が弱いフロアでは、`ble_scan` capabilityを持つWi-Fi接続ESP32をBLE scanner / relay
として配置できます。logical IDの例は`ble-relay-001`です。将来このNodeは接続不要の
SwitchBot advertisementを受信し、Wi-Fi/MQTTでGatewayへ中継します。MQTT topicと
payload schema、relay本体機能は未実装です。

## Build

```bash
cd firmware/esp32/omk-node
pio run -e atom-s3-lite
pio run -e m5stick-c
```

AtomS3 Lite、オリジナルM5StickCともbuild SUCCESSを確認済みです。AtomS3 Liteの
uploadとGatewayによるDiscovery v1検出も確認済みです。M5StickC Plus / Plus2は
別ボードprofileを検証してから追加します。

## Repository hygiene

- 追跡する: `src/idf_component.yml`、`dependencies.lock`、`sdkconfig.defaults`、
  `partitions.csv`、firmware source
- 追跡しない: `.pio/`、`managed_components/`、provisioning credential

managed componentはmanifestとlockから再現します。現在の`platformio.ini`は
`sdkconfig.atom-s3-lite`と`sdkconfig.m5stick-c`を`board_build.sdkconfig_defaults`として
参照しているため、これらを単純に生成物としてignoreする段階ではありません。board別に
必要な設定だけを小さなdefaultsへ整理し、`platformio.ini`を更新した後に、完全生成の
sdkconfigをignoreする方針です。ESP-IDF移行の作業tree整理はProvisioning機能の安定後に
行います。

## Troubleshooting

- **`intelhex` is missing**: pinned PlatformIO/ESP-IDF package setを整える。
- **`wifi_provisioning/...` header missing**: ESP-IDF 6では
  `espressif/network_provisioning`へ移行済み。manifestは`src/idf_component.yml`に置く。
- **legacy GAP symbolのlink error**: Bluedroid BLE-onlyとBLE 4.2 legacyのKconfigを
  有効化する。BLE 5 feature設定ではraw legacy advertisingと整合しない。
- **GCC 15の`old-style-declaration`が-Werrorになる**:
  `CONFIG_COMPILER_DISABLE_GCC15_WARNINGS=y`を維持し、ESP-IDF sourceを編集しない。
- **`No space left on device` under `/tmp`**: `/tmp/omk-platformio-701`等のPlatformIO
  temporary directory容量を確認する。OMK source起因ではない。
