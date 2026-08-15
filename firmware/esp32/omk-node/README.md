# OMK ESP32 Node

OMK NodeはAtomS3 Lite、オリジナルM5StickC、および将来のESP32-C3/S3
センサノードで共用するファームウェア基盤です。ボード固有のLCD、LED、ボタンは
この基盤に含めません。BLE中継ノード（`ble_scan`）と将来のSEN66ノードも同じ
基盤を使います。

この文書では、実装済みかつ実機で確認済みのDiscovery、Security 1
Provisioning、通常起動時のWi-Fi再接続を記録します。MQTTによるBLE中継、
Gateway/Dashboardからの登録自動化、logical IDの割当は未実装です。

## 対応環境

- PlatformIO: `espressif32@7.0.1`（ESP-IDF 6.0.1）に固定
- フレームワーク: ESP-IDF。公式`espressif/network_provisioning`を
  `src/idf_component.yml`で管理する
- Bluetooth: Bluedroid、BLE-only controller、BLE 4.2 legacy advertising
- 対応確認済みボード: AtomS3 Lite、オリジナルM5StickC

BLE 4.2 legacyを選ぶのは、Discovery v1が31-byte legacy advertisingと
Bluedroidのraw GAP APIを使うためです。NimBLEまたはextended advertisingへ
切り替える場合はプロトコル改訂が必要です。ESP32-S3のAtomS3 Liteでは8 MB、
オリジナルM5StickCでは4 MBのFlash設定をボード別に選びます。

`CONFIG_COMPILER_DISABLE_GCC15_WARNINGS=y`はESP-IDF 6のGCC 15互換設定です。
ESP-IDF本体を修正したり、広範な`-Wno-error`を追加したりしません。

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

## Discovery BLE v1

通常bootのNodeは、未Provisioningか保存済みWi-Fi credentialを持つかにかかわらず、
次のService UUIDのraw legacy advertisingを出します。Provisioning済みNodeでも
Discoveryは再Provisioning開始経路として維持します。

- Service UUID: `7d2a4d90-7b64-4e3a-9f37-95e77d7b5101`
- Service Data: 厳密に10 bytes
  `protocol_version(1) | provisioning_state(1) | capabilities(2, big-endian) | node_id(6)`
- 現在の値: protocol version `1`、provisioning state `0`（未登録として定義）
- Capability bits: `ble_scan = 0x0001`、`sen66 = 0x0002`。複数bitの組合せを許容する

Service Dataには`site_uuid`、SSID、Wi-Fi password、PoP、MQTT credential、
SORACOM情報その他の秘密情報を載せません。AtomS3 Liteのadvertising v1はGateway
管理画面で未登録OMK Nodeとして実機検出済みです。

Provisioning済みNodeも現在はstate `0`を広告するため、広告上の状態表現と実際の
Provisioning済み状態の整理は今後のプロトコル設計課題です。

接続後の開始要求用Control GATT serviceはadvertising payloadへ追加せず、service
discoveryで見つけます。

- Control Service UUID: `c1347091-4268-2fb1-884a-7d019a432154`
- START characteristic UUID: `c1347091-4268-2fb1-884a-7d019a432155`
- Property: write only。payloadは1 byteの`0x01`だけを受理する

不正長、値、prepared writeはGATT errorとして応答します。GATT callbackは検証、
応答、固定長4のFreeRTOS event queueへの投入だけを担当し、NVS操作、再起動、BLE
停止、Provisioning開始は行いません。`node_state_task`だけがeventを解釈します。

## DiscoveryからProvisioningへのboot境界

ESP-IDF 6ではBluetooth controllerの同一boot内でのdeinit後再initを前提にしません。
そのためDiscovery BLEとnetwork provisioning BLEを同時に動かしたり、同一boot内で
所有権移譲したりしません。

1. 通常bootはDiscovery BLE（raw advertising + Control GATT）を開始する。保存済み
   Wi-Fi設定があればWi-Fi STAも並行して開始する。
2. `0x01`はstate taskへ渡され、PoPが有効なら`omk/next_boot_mode`へuint8の
   `PROVISIONING`を保存・commitしてから再起動する。
3. Provisioning bootは最初にこのflagをeraseしてcommitする。クラッシュ、watchdog、
   電源断後にProvisioning boot loopへ入らないためである。
4. Provisioning bootではDiscovery BLEを開始せず、network provisioningだけが
   controller / Bluedroidを初期化する。通常boot用の`wifi_station`も開始しないが、
   network provisioning manager自身はWi-Fi / STAを初期化し、credentials apply後に
   接続する。

flag消去またはcommitに失敗した場合はProvisioningを開始せずsafe idleに留まります。
Discovery bootのClassic BT memory releaseはboot中に一度だけ行い、BLE-onlyとWi-Fi
STAの併用には影響しません。

## Security 1 BLE Provisioning

Provisioning bootは`network_prov_scheme_ble`と`NETWORK_PROV_SECURITY_1`のみを
使用します。Security 0 / Security 2へfallbackしません。`CONFIG_ESP_PROTOCOMM_SUPPORT_SECURITY_VERSION_1=y`
が必要です。

- Provisioning Service UUID: `c2f08e31-75fd-4f81-9e6d-4f89a3bc1d27`
- Service name: `OMK_<12 hex node_id>`（例: `OMK_9af9509eb8b6`）
- PoP: 前節のNode固有secretをlowercase hexへ変換したもの

DiscoveryのSTART操作は再Provisioningを明示する要求です。したがって既存Wi-Fi
credentialが保存済みでも、Provisioning bootではSecurity 1 BLE serviceを必ず開始
します。起動時に既存credentialを消去しません。

AtomS3 LiteではPC側credentialのPoPを用いたSecurity 1 session、SSID/passwordの
保存、直後のWi-Fi接続を実機で確認済みです。SetConfigおよびApplyConfigはいずれも
成功しました。

### Windowsでの手動確認

WSLには通常BLE adapterが渡らないため、Security 1 clientはWindows側のPython 3.13
環境で実行します。Espressif公式`esp_prov`コードと`bleak`、`protobuf`、
`cryptography`を使います。Windowsの標準clientがpair処理で停止する場合は、pairを
強制しないBLE read/write経路で確認できます。Control characteristicへの`0x01` write
はNodeの再起動を起こすため、Windows側が"user cancelled"と報告しても、その後に
Provisioning advertisementが出れば開始要求は成功しています。service nameが取得
できない場合もBluetooth MACで対象を識別して確認できます。

## 通常bootのWi-Fi STA

`wifi_station`モジュールは保存済みcredentialの有無を確認し、未設定ならエラーに
せずDiscovery BLEだけを継続します。設定済みなら`esp_netif`、default event loop、
default STA netif、Wi-Fi driverを初期化してSTA接続を開始します。切断時は通常運用の
再接続を行い、`IP_EVENT_STA_GOT_IP`で成功を記録します。SSID/passwordはログしません。
Wi-Fi初期化が失敗してもDiscovery BLEは可能な限り継続します。

AtomS3 Liteでは再起動後およそ15秒でRaspberry Pi APへ再接続することを確認済みです。
AP側はSTA MAC `ac:a7:04:03:d7:f8`を観測し、`192.168.50.175`はreachable、pingは
4/4応答・packet loss 0%でした。Provisioning bootでは通常boot用の`wifi_station`と
Discovery BLEを開始せず、BLEはnetwork provisioningが単独で所有します。Wi-Fi / STAは
network provisioning managerがcredentials apply後の接続のために初期化します。

## OMK networkと今後のNode

Raspberry PiはOMK専用APを提供し、NodeはそのWi-Fiへ接続します。SSIDは将来
`omk-`とsite UUIDの短縮値で構成する案がありますが、OSSは任意の識別子・非中央管理
APでも使える設計を維持します。ランダムなWi-Fi passwordは公開識別子ではありません。
将来は管理画面からSecurity 1 Provisioningを操作し、タブレット向けにはQR導線を
追加します。APのLAN client側をSORACOM外部通信へ転送する機能は対象外です。

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
