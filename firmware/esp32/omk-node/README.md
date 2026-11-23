# OMK ESP32 Node

AtomS3 Liteの開発版はNative ESP-WIFI-MESHでSEN66の計測とSwitchBot BLE中継を行います。非RootはBLE観測と計測をMeshで送り、Rootは自機の計測と子NodeのデータをGatewayのMQTTへ渡します。Rootは子NodeからのBLE観測を中継する間、自機のBLEスキャンを休止します。[通信方式と運用上の制約](../../../docs/decisions/esp-wifi-mesh-node-networking.md)

## 2026年11月の実機運用版

| 項目 | 確認内容 |
| --- | --- |
| アプリ | `V2D-P1-stable-p1`、ESP32-S3、ESP-IDF 6.0.1 |
| 実機に書き込んだBIN | 1,586,288 bytes、SHA-256 `d04f6d1de04834941574ff61d44c40c0758d53dd41f52ba3c06a001c5163ab2a` |
| 対応するELF | SHA-256 `0a47b6df2d6f743c23b713a87fa61c1467fe9205d05dcd73f7a61b3c185c7d97`（BIN内の値と一致） |
| ソース | `5f39da5e47bf6dadcb1b4df11b74d58df7d66379`。EVOに保存されたソース140ファイルが一致 |
| 実機 | 11月22日にSEN66-001、SEN66-002、階段下、階段上の4台へ導入。書き込み時のハッシュ検証出力はSEN66-001で確認、残る3台は作業者の完了記録による |

EVOの保存ソースを当時の実効設定・依存関係で再ビルドし、バイナリの長さとアプリ本体を照合しました。保存BINとの差は埋め込みELFハッシュとイメージ末尾の検証情報に限られました。mainはこの通常構成を採用しています。試験時に有効だった通信の計数・観測処理はビルドに含まれます。実機で無効だったpanic採取機構とその専用パーティションは除外しました。

統合後のソースも同じESP-IDFビルド条件で照合しました。実機BINと各セグメントのアドレス・長さは一致し、ELFハッシュと末尾の検証情報を除く差は13バイトでした。逆アセンブルで、いずれも `main.c` の `ESP_ERROR_CHECK` に渡すソース行番号の変更と確認しました。PlatformIOの通常ビルドも成功し、生成したパーティションテーブルは実機版のビルド成果物と一致しました。

DashboardのUSBセットアップが使う公開prebuiltは `021ff22` 対応の旧Mesh版です。Native Meshへの切り替えには同一Mesh内のNodeを揃え、Gatewayに `f6a0f8b` 以降のBLE Sensor Managerを使用します。公開パッケージの由来と配布条件は別途記録しています。[公開prebuiltの記録](docs/release-021ff22.md)

## 有線センサの識別（M4）

SEN66はI2C ACKだけでなく、CRC等を検証したProduct Nameの完全一致後に初期化する。共通driver registry、再探索、将来のI2C/UART/analog sensor追加時の契約は[Node sensor discovery設計](../../../docs/decisions/node-sensor-discovery.md)を参照。analogは汎用profileによる明示設定を前提とし、ADC値から機種を推定しない。通常起動はStop/Startを使い、持続異常へのデバイスリセットは1回に制限する。利用可能なチャネルの計測は継続する。

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

GatewayのCLI/system-managerとNode通常firmwareを両方更新する。system-manager更新後はサービスを再起動する。既存Nodeの通常firmware更新は[「OMK Nodeを更新・再設定する」](../../../docs/user/node-maintenance.md#ファームウェアを更新する)の通常更新手順を使い、保存済みcredential、Wi-Fi、Logical IDを維持する。新品Nodeの初回導入は[「3-2. SEN66 Nodeをセットアップする」](../../../docs/user/esp32-node-setup.md#2-dashboardからセットアップする)を標準とする。使用済みNodeを新Gatewayへ設定し直す場合は[「OMK Nodeを更新・再設定する」](../../../docs/user/node-maintenance.md#既存nodeを再セットアップする)の再セットアップ手順を使う。この再セットアップはcredentialを置換し、Wi-Fiを再設定、Logical IDを解除するため、通常更新とは区別する。探索用identifyだけはv1を受け付け、旧firmwareも台数に数える。旧firmwareを選択した場合は操作用v2確認で停止し、credentialを送信しない。旧CLIから新firmwareへのv1状態変更要求も拒否する。DiscoveryはBLE v1、USB provisioningはv2、Native Meshの登録メッセージはv2を使用する。[検証手順](../../../docs/decisions/usb-serial-provisioning.md)を参照する。

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

再セットアップ専用factory recordは`OMKR` + 32-byte PoPです。通常の`OMKP` importと区別し、ネットワーク開始前にNodeの論理登録と `omk_net/gw_cred` をクリアし、`omk/prov_pop`を置換してread-backします。旧ESP-IDF STA設定がlegacy migrationで復活しないよう、`omk_net/reset_sta`でSTA設定のクリアを次のWi-Fi初期化まで引き継ぎます。ESP-IDF APIでSTA設定だけをクリアし、NVS namespace全体やflash全体は消去しません。診断情報等の無関係なNVSは保持します。すべてのimport処理が成功するまでfactory recordを保持するため、途中の電源断では再実行できます。

Gatewayは書込み前に新credentialをmode 0600でatomic保存し、`setup_state=reinitialize_pending`を記録します。失敗後はファイルを削除せず、同じ`--reinitialize`で再試行します。同じPoPでfactory recordを再書込みし、firmware更新後にUSBの`verify_reinitialize`で新PoP・Wi-Fi未設定・Logical ID未登録を確認してからWi-Fiを投入します。新しい`provisioned` statusを受信して初めてpendingを解除します。pending中の通常更新は拒否します。PoPは既存方式の保存情報であり、USB provisioning自体をPoP認証へ変更するものではありません。

Dashboardの再セットアップも専用APIと確認画面を使用します。配置済みprebuiltが`verify_reinitialize`未対応なら、書込み前に停止します。対応するprebuilt packageはsource commit後に既存のpackage生成手順で更新してください。未commitのsourceを試験するCLIは、ローカルbuildから一時packageを作り、公開prebuiltを書き換えません。

rawな`pio run -t upload`やesptool eraseは正式更新手順ではありません。

通常更新の書込み先はbootloader（`0x0`）、partition table（`0x8000`）、app（`0x10000`）です。現行partition tableのNVS（`0x9000`〜`0xefff`）とfactory secret（`0xf000`〜`0xffff`）を含まず、Wi-Fi credentialとLogical IDを消去しません。通常起動もNVS全消去を行いません。DashboardのUSB setupも既存Nodeではこの3領域だけを書き込みますが、その後にGatewayのWi-Fi情報を設定し直す処理があります。既存Wi-Fi設定をそのまま保持するfirmware更新には、上記の通常更新手順を使います。

登録済みNodeはNVSから復元した `logical_id` とrevisionをRoot経由でretained `registration/status` に送るため、新品Gateway・新brokerでも再登録操作や旧ACKなしでLogical IDを復元できます。これは通常更新scriptのcredential検証とは別の仕組みです。更新済みNodeの状態復元自体に旧Gatewayのファイルは必要ありません。`provisioned`状態ではIDを出力せず、再接続時に設定ACKを作り直すこともしません。

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
| `omk_diag` | data / 0x41 | `0x210000` | `0x2000` |

`platformio.ini`の`board_build.partitions = partitions.csv`とESP-IDF側のcustom partition設定の両方が必要です。PlatformIOのデフォルトsingle-app tableでは`0xf000`は`phy_init`であり、custom tableが実機へ書かれていない状態でそこへ secretを書き込むことは危険です。書込み前に生成物・実機双方のpartition tableを確認します。

### 開発時だけの登録状態reset

Dashboard登録フローを再試験する場合だけ、次を明示的に実行します。

```bash
./scripts/reset-omk-node-registration.sh atom-s3-lite /dev/ttyACM0 --confirm
```

このスクリプトは一時的な開発用imageをflashし、NVS namespace `omk`の登録状態を未登録にしてrevisionを進めます。保存結果を読み戻した後、通常firmwareを自動的に戻します。NVS partitionの全消去、Wi-Fi credentials、`prov_pop`、`factory_secret` partitionへの操作はしません。通常firmwareにはこの操作へ到達するruntime経路はありません。

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
- Provisioning state: `0` = unregistered、`1` = provisioned、`2` = registered。保存済みWi-Fi credentialと登録状態から決定する。
- Capability bits: `ble_scan = 0x0001`、`sen66 = 0x0002`。複数bitの組合せを許容する

Service Dataには`site_uuid`、SSID、Wi-Fi password、PoP、MQTT credential、SORACOM情報その他の秘密情報を載せません。AtomS3 Liteのadvertising v1はGateway 管理画面で未登録OMK Nodeとして実機検出済みです。

旧方式では、次のControl GATT serviceを再Provisioningの開始要求に使用していました。現在のfirmwareにはこのGATT serviceも開始処理もありません。再設定にもUSB Serial/JTAGを使用します。以下は旧実装の識別情報です。

- Control Service UUID: `c1347091-4268-2fb1-884a-7d019a432154`
- START characteristic UUID: `c1347091-4268-2fb1-884a-7d019a432155`
- Property: write only。payloadは1 byteの`0x01`だけを受理する

旧実装では不正長、値、prepared writeをGATT errorとして返し、GATT callbackから固定長4のFreeRTOS event queueを経由して`node_state_task`へ開始要求を渡していました。

## Build

```bash
cd firmware/esp32/omk-node
pio run -e atom-s3-lite
```

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

配布済みバイナリの第三者許諾は[第三者通知](prebuilt/atom-s3-lite/THIRD_PARTY_NOTICES.md)と[監査記録](LICENSE_AUDIT.md)に記載しています。
