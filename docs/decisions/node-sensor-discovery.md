# Node有線センサのdiscovery / identity（M4）

## 境界と今回の実装

I2C addressへのACKはendpointの存在確認であり、model identityではない。探索ではread-onlyのidentity commandを優先し、機種確定前にreset、設定変更、measurement startを送らない。値がもっともらしいことも機種識別根拠にしない。

`firmware/esp32/omk-node/src/`の責務は次のとおり。

| ファイル | 責務 |
| --- | --- |
| `sensor_driver.h` | transport / endpoint、identity結果、driverのidentify/start/read/publish/close契約 |
| `sensor_driver.c` | 全候補driverのevidenceを評価して選択 |
| `sensor_drivers.c` | production driver registryとSEN66の既存MQTTへのadapter |
| `sensor_manager.c` | endpointごとのcontext・接続状態・計測・再探索・復旧 |
| `sen66_sensor.c` | SEN66の存在確認、Product Name検証、初期化、計測 |

endpointはtransport、handle、I2C address / UART port / ADC channelのunionを持つ。addressは候補driverを絞るためだけに使用する。現在のboard profileはI2C `0x6B`の1 endpoint、production registryはSEN66の1 driver。managerはendpointごとのslotを扱い、機種名による分岐を持たない。現時点でUART/ADCの探索・計測や設定保存を実装したわけではない。

identityは`EXACT`、`AMBIGUOUS`（familyを含む）、`NO_MATCH`、`ERROR`（不正応答含む）、`CONFIGURATION_REQUIRED`を区別する。複数exactまたはfamily evidenceが残ればambiguous、他候補のerror/configuration requiredが残れば自動選択しない。全候補を評価し、唯一のexactだけを採用する。driverの順番を変えても選択は変わらない。

他protocolへのprobeがtimeoutしたことを機械的にno-matchへ弱めない。このため将来、別driverのexactとSEN66 probe errorが同時に出るendpointも未認識になる。その場合は安全性を確認した共通protocol discriminator、または明示profileによる候補制限を追加する。未知deviceへの全commandの無害性はaddressだけでは保証できない。driver追加時には同じendpointの他機種に対してもprobeが設定変更等にならないか確認し、根拠がない組合せは自動探索へ入れない。

## SEN66の識別と復旧

1. `0x6B` ACK後、仮のdevice handleで`Get Product Name (0xD014)`を読む。
2. 32 data bytes（CRC込み48 bytes）を要求する。通信失敗、各2 bytesのCRC不一致を拒否する。CRCはinit `0xFF`、polynomial `0x31`。
3. NULまでの文字をprintable ASCIIとして検証し、空文字・NUL欠落を拒否する。NUL後のpaddingは規定しないが、全wordのCRCは必須。
4. `SEN66`の完全一致だけexact。`SEN63C` / `SEN65` / `SEN68`はfamily/ambiguous。他文字列や`SEN66-extra`をSEN66にしない。
5. 選択後も状態変更直前に再識別する。確認できたSEN66にのみstopを送り、1.4秒待ち、再度identityを検証してreset / startへ進む。

ESP-IDF receive APIには実受信長の返却がないため、固定48 bytesの成功と全word CRCを要求する。未受信領域は`0xFF`で初期化し、短い応答を補完して正常扱いしない。Product Nameはmodel identityであり、個体serialを意味しない。`0xD033` Serial Numberは今回使わない。

SEN66が計測を継続したままMCUだけ再起動した場合やliveness復旧でも、idle専用のresetを安全に行えるようstopを入れる。既にidleならstopが失敗する場合を許容するが、その後のidentityとresetの成功は必須。探索中のstopは行わない。

計測前にもProduct Nameを読み、別機種や不正identityならその周期の値をpublishせず、即座にconnected=falseとしてlocal handle/contextを解放する。recovery時には古い機種だと仮定したstopを送らない。60秒後の再探索でexactを得てから初期化し直す。通常readの3回連続失敗と60秒measurement liveness timeoutも同じ再探索へ進む。未接続時も60秒ごとに探索するので、relay Nodeへの後付けに対応する。

計測待ち10秒、成功計測を基準としたliveness、Logical ID待ち、MQTT失敗時の継続、既存measurement変換・topic/payload・diagnosticsは維持する。確認とreadの間の瞬間的な交換までI2C transactionで原子的に防ぐものではない。同型機種への交換もserialで追跡しない。

## 将来driverを追加する際

registryへdescriptorとendpointごとのcontextを追加し、read-only identifyと確定後startを分ける。closeは交換先へ旧protocolのcommandを送らず、local resourceを解放する。復旧時に停止が必要なら、start側で再識別後に行う。現在のmanagerが要求するread/publish/connected/diagnostics callbackも実装する。

### SEN0466等のprotocol識別

DFRobot公式資料ではSEN0466はI2C/UARTを扱い、address group 3には`0x68` / `0x69` / `0x6A` / `0x6B`がある。従って`0x6B`はSEN66専用ではない。

公式MultiGasSensor実装の`queryGasType()`は濃度照会protocolの応答からgas typeを読む。将来driverでは少なくとも次を独立に検証する。

- 設定済みinterface / endpoint。UARTはport、baud等を明示し、未知portへ無差別にcommandを送らない。
- 9-byte frameの完全受信、先頭・応答commandとの対応（passive濃度照会は`FF 86`）、checksum。
- gas typeフィールド（byte 4、COは`0x04`）。checksumはSensirion CRCとは別の加算checksum。
- I2Cのregister/framingとUARTのactive/passive応答を混同しない。公式libraryの返却lengthを鵜呑みにせず実受信長を検証する。

これらはprotocol / CO familyの根拠であり、SEN0466というSKUの唯一性を保証する根拠ではない。固有model evidenceが不足する場合はfamily/configuration requiredとする。mode、address、alarm等を変更してidentityを得るprobeは採用しない。今回のテストには同じaddressの架空の第2driverを入れているが、SEN0466の実protocol実装・実機互換性試験ではない。

### 自動識別できないanalog sensor

特定製品向けの例外を作らない。電圧値やADC値からmodelを推定しない。現在はanalog endpointを必ず`CONFIGURATION_REQUIRED`とし、driverの自動probeも実行しない。UARTも今回の自動探索対象外で同じ結果とする。

将来はversionedなboard/利用者profileで、driver/model、物理channel、測定量・単位、入力電圧上限、ADC設定、基準電圧、変換式と係数、校正、許容範囲を明示する。profileの機種指定は「実物を自動識別したevidence」と区別する。未設定・不整合・未対応profileでは開始しない。profile変更時は古いcontext・測定値・Logical IDとの結び付けを再評価する。profileの保存/UIとADC measurement driverは今回未実装。

OMRONの公式D6F-W/V資料はアナログ電圧出力の例である。D6Fシリーズにはinterfaceの異なる製品があるため、シリーズ名だけでI2C対応や自動識別可能性を仮定しない。同じ設計方針を他社のアナログ電圧出力センサにも適用する。

## Gateway互換性と残課題

SEN66 adapterは既存`mqtt_registration_set_sen66_connected()`、diagnostics、publish関数を呼ぶ。Node registration、Logical ID、`connected_sensors`の生成経路は変更しない。capabilityはfirmwareの対応能力であり、現在接続されているsensorではない。未識別endpointをconnectedとして報告しない。後付け時は識別・開始成功によるconnected statusで既存Node一覧の登録操作へつながる。

slot構造は複数endpointを持てるが、現在のNode登録は1 Logical ID、connected statusもSEN66中心である。第2production driverやmulti-sensorを有効化する前に、endpoint/modelとLogical IDのbinding、sensorごとのconnected/diagnosticsとMQTT schemaを設計する必要がある。別modelに旧SEN66 Logical IDを渡す実装を追加してはならない。今回はGateway/UI/schemaを拡張しない。

同一bus/addressに2個体を並列接続すると、CRC異常等で拒否できても、整合した同一応答が重なるケースをソフトウェアで必ず検知できるとは限らない。物理address conflictは別bus、mux、address設定等で解消し、解消不能ならconfiguration requiredとして運用する。

## 根拠・検証

公式資料確認: 2026-09-10。

- [Sensirion SEN6x datasheet v0.9 Engineering Samples](https://sensirion.com/media/documents/057555E9/673DA8E3/Sensirion_Datasheet_SEN6x_v0.9_Distribution_Engineering_Samples.pdf): §4.8.18 Product Name（idle/measurement両方、20ms）、§4.9 CRC、stop/reset/startの条件。取得できた版はengineering sample資料であり、量産実機確認の代用とはしない。
- [Sensirion公式SEN66 C driver](https://github.com/Sensirion/embedded-i2c-sen66/blob/master/sen66_i2c.c): `sen66_get_product_name()`の32-byte読取りと20ms待機。
- [DFRobot SEN0466公式資料](https://wiki.dfrobot.com/sen0466/docs/24489)、[公式MultiGasSensor実装](https://github.com/DFRobot/DFRobot_MultiGasSensor/blob/master/DFRobot_MultiGasSensor.cpp): interface/address、gas type、framing/checksum。
- [OMRON D6F-W/V user manual](https://omronfs.omron.com/en_US/ecb/products/pdf/en-D6F-W_D6F-V_users_manual.pdf): アナログ出力例。D6F-W固有driverを今回実装する根拠には用いない。

`tests/test_sensor_identity.py`はproductionのmanager・registry・SEN66 Cコードをホストでコンパイルし、模擬I2C/time/MQTTで実行する。exact、未知device、別SEN6x、CRC・長さ・ASCII異常、初期化前の交換、複数driver競合、明示設定必須、未接続・再接続・別機種交換、liveness、MQTT/Logical ID待ちを検証する。実機操作は行っていない。新しいProduct Name確認と復旧シーケンスは実機再試験が必要。
