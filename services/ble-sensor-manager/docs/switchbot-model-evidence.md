# SwitchBot model evidence と payload validation（M1 / M2）

## 判定と保存

機種選択は `switchbot._select_model()`、選択済み形式の復号は `_decode_selected()`、
数値の妥当性は `_values_valid()` に分ける。長さや正常値から機種候補を順番に試す
処理は廃止した。特に12-byte manufacturer dataはPlug / Outdoor両方の値として
成立し得るので、値の妥当性を機種固有性とみなさない。

1. 対応するfd3d service device typeを優先する。型を先に選び、その形式だけを検証する。
2. 登録済みphysical deviceのmodel hintと明示typeが矛盾したらunknownにする。
   正常なmanufacturer layoutでも矛盾を救済しない。
3. serviceがない場合だけ、既存registryのhintで選んだmanufacturer形式を検証する。
   Presenceの実測`00 20` service付き形式、およびPi内蔵Bluetoothでserviceが取得できない
   12-byte manufacturer-only形式は機種固有と確認できないため、Presence hintがある場合に
   限って対応形式を検証する。
4. 未登録のmanufacturer-only packet、未知service type、矛盾する複数service fieldは
   unknown。辞書・decoderの並び順で決めない。未知serviceを無視してhintへ戻らない。
5. 選択した形式や値が不正ならpacket全体をunknown・空valuesにする。別modelを再試行しない。

unknownはraw確認用candidateには表示するが、自動のID提案・登録・MQTT測定値
publishは不可。下記の未確認候補の明示選択に限り、利用者が機種を確認した上で登録できる。rawの`classification`に`evidence`と`status`を残す。
`invalid_payload`、`conflicting_model_hint`、`ambiguous_service`等を区別できる。
directの登録済み一覧には矛盾・不正packetの値を渡さず、`status=unrecognized`とし、管理画面は
「機種・データ未確認」と表示する。受信していることと正常に復号できることを区別する。

BLE address、manufacturer先頭6 bytes、`device_key_for()`、registered sensor IDの
生成・対応規則は変更しない。Meter / Meter Plusの内部modelを共通にする既存設計と
registryの旧名migrationも維持する。API schemaを増やして機種名を分割しない。

## 根拠の分類

Aは公式BLE仕様、Bは複数実機、Cは1個体の実測、Dは固有性未確認のコード上の仮定。
**公式配下の実装コードは公式仕様書とは区別**し、参照commitと対象形式を限定する。
既存captureの個体数・packet数は[過去のraw監査](switchbot-raw-capture-audit-2026-08-20.md)
の記録に基づく。今回、新しい実機captureは行っていない。

| 製品 / 内部model | 機種選択の根拠 | 復号形式と限界 |
| --- | --- | --- |
| Meter / `temperature_humidity_sensor` | A: service `0x54` | 6-byte serviceの温湿度・battery。C: manufacturer 11 bytes / index 7=`03`は既存hint時の形式条件だけ |
| Meter Plus / 同上 | A: service `0x69` | Meterと同じ6-byte値形式。service typeは区別できるがOMK schemaは意図的に共通。別個体実測の根拠は今回確認できていない |
| Meter Pro CO2 / `co2_sensor` | 参照した公式実装: service `0x35`、既存hint、または利用者明示確認 | B: 2個体・6 captureの16-byte manufacturer形式。可変index 6/7/11/12を固定しない。末尾`00`は観測上の形式条件で、固有model IDではない。7-byte serviceの別broadcast modeは未対応 |
| Motion / `motion_sensor` | A: service `0x73`、または既存hint | 公式6-byte service単独でも復号可能。C: manufacturer 10 bytes / status下位6 bit=`2c`はhint時の対応形式。index 8の`da`等も受容 |
| Presence Sensor Pro / `presence_sensor` | 参照した公式実装: service `0x70`。実測の`00 20` service付き・manufacturer-only形式は既存hintまたは利用者明示確認が必須 | C: 12+7 bytes、およびPi4内蔵Bluetoothで得た12-byte manufacturer-only。公式実装のstatus/照度のbit解釈と実測を使用する。長さの組合せだけで初回識別しない。`0x70`形式は参照実装由来の合成テストであり、今回の実機確認ではない |
| Contact / `contact_sensor` | A: service `0x64`、または既存hint | 完全な9-byte service。暗号化bit付きは未対応。manufacturer 13 bytes / status下位nibble=`c`は既存fixture系列に基づくC相当で個体差未確認。serviceの状態が古い実測があるため、同じmodelに選択した後は妥当なmanufacturer状態を優先 |
| Plug Mini / `plug_sensor` | A: `0x67`。参照公式実装・B: 国内複数個体の`0x6a` | 3-byte service、12-byte manufacturer。stateと電力の解釈はA。index 8の`10` / `16`や電力末尾`00`を機種IDとして使わない |
| Outdoor Meter / `waterproof_sensor` | `0x77`: 公式repo内の**非公式community補足**、参照公式実装、CのOMK実測 | 3-byte service、12-byte manufacturer。温湿度を復号。末尾`00`は対応形式条件だけ。型なし未登録packetはunknown |

Dだった「正常範囲なら機種確定」「12+7ならPresence」「末尾0ならOutdoor」
「16 bytesならCO2」「`0x16`ならPlug」は、初回model判定には使用しない。
登録済みhintは追加情報であり、layout自体に固有性を与えるものではない。

参照した一次資料:

- [SwitchBot BLE API device types](https://github.com/OpenWonderLabs/SwitchBotAPI-BLE/tree/latest)
- [Meter仕様とOutdoor community補足](https://github.com/OpenWonderLabs/SwitchBotAPI-BLE/blob/latest/devicetypes/meter.md)
- [Motion仕様](https://github.com/OpenWonderLabs/SwitchBotAPI-BLE/blob/latest/devicetypes/motionsensor.md)
- [Contact仕様](https://github.com/OpenWonderLabs/SwitchBotAPI-BLE/blob/latest/devicetypes/contactsensor.md)
- [Plug Mini仕様](https://github.com/OpenWonderLabs/SwitchBotAPI-BLE/blob/latest/devicetypes/plugmini.md)
- [既存Presence decoderが参照していた公式実装commit](https://github.com/OpenWonderLabs/node-switchbot/blob/d2eafbaa7bee75a907633f108329b3716c61e82b/src/device.ts)

参照commitではCO2=`5`、Outdoor=`w`、Presence=`p`。
一方、新しいSDKの[enum](https://openwonderlabs.github.io/node-switchbot/enums/SwitchBotBLEModel.html)
には異なる割当てがある。SDKのenumだけを新しいwire typeの証拠としてalias追加しない。
このdecoderは上表の対応形式を対象とし、例えば`0x77`に16-byte CO2 layoutが来ても
Outdoorとして値を返さずunknownにする。全firmware・全broadcast modeへの保証ではない。

## 値の検証

- battery / RHは0〜100%。温度小数のBCD相当nibbleは0〜9。flag bitは仕様に従って分離する。
- 温度はOMKの既存受入範囲−20〜60℃、CO2は400〜10,000 ppm。物理的に不可能という
  断定ではなく、この形式でOMKが受け入れる範囲である。範囲外をclampしない。
- Contact HAL state 3は拒否。Motion serviceは未対応のreserved status/light/distanceを拒否。
- Presenceはstatusの既存妥当性確認を維持し、motionはstatus bit 6から得る。末尾byteは
  bit 7をLED state、bit 0〜3を照度levelとして扱う。bit 4〜6は未解釈であり、立っていても
  packetを拒否しない。照度は0〜15のlevelでありluxではない。serviceがある形式だけbatteryを
  得て、manufacturer-only形式には`battery_percent`キーを作らない。serviceの未解釈tailを
  機種IDや測定値と推測しない。
- Plugはstate `00` / `80`を検証。電力のoverload bitを分離し0.1 W単位で復号する。
  0 W、低負荷、flag/counterが0のpacketも型またはhintがあれば扱える。
- 不正serviceがあれば正常manufacturerでも救済しない。供給されたmanufacturer測定
  payloadが選択modelに適合しなければ、正常serviceへのfallbackもしない。

## 登録済みhintと移行

hintは通常と同じphysical keyを作ってregistryから読み取る。新規candidateから
manufacturer推定でhintを作成・永続化しない。矛盾時にregistryを書き換える処理もない。
過去の誤登録が疑われる場合は実機の製品名とpacket evidenceを照合し、明示的な
serviceで安全に再識別できることを確認してから、管理者が登録解除・再登録する。
履歴データの自動修正は行わない。

従来manufacturer-only推定で新規登録できた機器は、serviceが取れなければ今回から
unknownになる。Presenceの実測`00 20`形式も未登録時は同様。既存登録は対応形式・
値が妥当なら継続利用できるが、既存hint自体が誤っている場合、矛盾する明示serviceが
来ない限り自動検出できない。対応形式の取得を確認する前に既存登録を削除しない。
未認識の新規個体を長さだけで自動判定しない。Presence、Meter、CO₂の明示選択方式は下記に限定する。

## direct / relay

BlueZ/Bleakのdirect callbackはmanufacturer/service辞書を共通decoderへ渡す。
NodeはAD type FFのcompany `0x0969`とtype 16のUUID `fd3d`を取り出し、company ID / UUID
を除いたpayload、BLE address、RSSIをprotocol v1のJSONで送る。上表のpayloadは
manufacturer最大16 bytes、service最大9 bytesで、各31-byte上限とJSON 512-byte上限内。
Gatewayは長すぎるデータを切り詰めず拒否し、UUIDの大小文字を正規化した重複も拒否する。

relayは同じBLE addressのADV / SCAN_RSPを結合する。fragment間に2秒を超える無受信期間が
あれば以前のfieldを破棄する。これはfieldごとの厳密な最大経過時間ではなく、連続受信中は
以前の片側fieldが残る可能性がある。原子的な同一packetの証明ではない。
受信fieldが同じならdirect/relayのmodel/value/keyは同じ。情報欠落ではunknownを許容し、
別modelへのメーカーlayout推定は行わない。relayのunknownは測定処理へ渡さない。
従来どおり登録済み一覧のlatestはdirect受信だけで更新する。relayによるMQTT測定値と
この一覧の受信状態は別で、relayだけの不正packetで一覧をunrecognizedには更新しない。
firmware、relay protocol、slot数、送信頻度は変更していない。

## 自動テストと実機確認計画

自動テストは衝突、hint矛盾、不正数値・状態、service欠落、同長未知payload、全8製品の
入力経路・MQTT・登録候補・登録済み一覧を確認する。既存captureの識別子は例示用に
置換し、温湿度・sequence・counter・state等の可変byteは別テストで変化させる。
MACのみの置換テストはidentity独立性の検査として区別する。relay C sourceは変更せず
ホストでfake clock/MQTTとコンパイルし、fragment完成前・結合後・gap失効後を確認する。

**エージェントによる実機操作は未実施**。候補版に対する利用者のPresence試験結果は下記に記録する。
過去の記録・利用者の試験・今回の自動テストを混同しない。
管理者が手元の個体で以下を確認する。新規captureを共有する場合は識別子を匿名化する。

| 機種 | 実機で確認すること | 別個体・firmware差 |
| --- | --- | --- |
| Plug Mini | service付きで新規識別、0 W/低負荷/ON-OFF、service欠落時は登録hintで継続。Outdoor表示にならない | 過去は複数個体、今回未実施。別firmwareを推奨 |
| Outdoor Meter | service `77`、温湿度・負温度、Plug風末尾値のときもservice/hintと一致 | 過去は1個体、2個体目を推奨 |
| Presence Sensor Pro | `00 20`のunknown候補から機種を明示確認して新規登録、再起動後のhint・在不在・battery・照度。不正値を蓄積しない。`70`形式は存在を含め別途確認 | 利用者が既存hint時の正常動作に加え、公開前の標準Pi4 fresh Gatewayで旧registryなしの初回登録を確認。受信形式・登録方式の別は未記録。別個体・modeを推奨 |
| Meter | `54` serviceの取得可否、manufacturer-onlyの未確認preview・明示登録、温湿度変化 | serviceなしで24.0 ℃ / 52 %の一致を利用者が確認。別個体を推奨 |
| Meter Plus | `69` service、温湿度・battery、共通内部modelと正常表示 | 別個体実測の確認資料なし。実機試験を推奨 |
| Meter Pro CO2 | `35` short service、manufacturer-onlyの未確認preview・明示登録、温湿度・CO2と本体表示の照合 | serviceなしの2台目で766 ppm / 25.5 ℃ / 45 %の一致を利用者が確認。異なるfirmware/modeを推奨 |
| Motion | `73` service単独、manufacturer-onlyはhint、検知/不在遷移、照度設定 | 過去は1個体、別個体を推奨 |
| Contact | `64` service、閉/開/閉じ忘れ、manufacturerとserviceの時間差、暗号化modeの拒否 | 既存fixture系列のみ。別個体を推奨 |

各試験で同じ内容のdirect/relay受信を比較し、登録候補・Dashboard値・MQTT・保存先に
誤modelの値が出ないことを確認する。受信fieldが違う場合はunknownを許容する。
残る限界は、未検証firmware/encoding、既存誤hint、BLE identityの偽装、fragmentの時間差。
今回、暗号学的identity確認や全SwitchBot model間のwire type一意性を証明したものではない。


## Presence service付き・manufacturer-only形式の初回登録

利用者がM1/M2候補版をGatewayで試験し、既存hintで在不在・照度・batteryの更新が
正常である一方、1台の登録を削除すると`unsupported_service_type`でunknownになると報告した。
今回報告されたrawの範囲は7-byte serviceの`00 20 xx xx xx xx xx`であり、完全な新規rawや
firmware番号は提供されていない。過去captureと先頭・長さは一致するが、残りのbyteまで
一致した、別個体を確認した、とは扱わない。

過去の保存済み監査・fixtureではserviceは`00 20 64 01 10 cc c8`。
manufacturerは先頭6-byte identityの後に`20 8c 00 04 00 8c`、
`1b cc 00 08 00 8c`等を観測した。

今回の追加実測は、新品microSDから構築したRaspberry Pi 4 Gateway、SwitchBotアプリで
Presence Sensor Proと確認した1個体で行われた。Pi内蔵Bluetoothではservice dataは取得できず、
匿名化した6-byte identityに続く12-byte manufacturer広告を継続受信した。未検出時は
`… 0a 8c 00 35 00 91` / `… 0e 8c 00 39 00 91`、検出時は
`… 0f cc 00 00 00 91` / `… 0f cc 00 01 00 91` / `… 0f cc 00 08 00 91` /
`… 0f cc 00 10 00 91`だった。実機操作でstatusが`0x8c → 0xcc`へ変化することと検知が
一致したため、既存どおりstatus bit 6をmotionとして使用する。末尾`0x91`はLED bit 7と
照度level 1を含む。これは1個体のlayout解釈の根拠であり、Presence固有の自動classifier根拠ではない。

| 部分 | 根拠と扱い |
| --- | --- |
| manufacturer index 6 | sequence（参照実装、実測で変動）。固定しない |
| manufacturer index 7 | adaptive / motion / battery range bits（参照実装）。在不在で`8c`/`cc`が変化。正常範囲は識別証拠ではない |
| manufacturer index 8/9 | 実測で`00 04`/`00 08`。固有性未確認、固定しない |
| manufacturer index 10 | trigger flag（参照実装）。実測の0を識別条件にしない |
| manufacturer index 11 | LED / light（参照実装、実測）。照度で変化、固定しない |
| service index 0/1 | `00 20`を同一個体で観測。機種固有という公開根拠なし |
| service index 2 | battery。過去は100%、今回利用者は更新を確認。0〜100を検証し固定しない |
| service index 3〜6 | 過去は`01 10 cc c8`。意味・個体差・firmware差が不明、固有IDとして使わない |

再調査した[公式BLE資料の機種一覧](https://github.com/OpenWonderLabs/SwitchBotAPI-BLE/tree/latest/devicetypes)
には、このPresence形式の公開仕様を確認できなかった。
旧参照commit `d2eafbaa`は`p` (`70`)からPresence parserを呼び、上記のmanufacturer値を
復号するが、`00 20`を一意に選択する規則はない。
現行参照commit [`0d2e215d96ae5d53e9467a70c99dc6747e457d3e`](https://github.com/OpenWonderLabs/node-switchbot/tree/0d2e215d96ae5d53e9467a70c99dc6747e457d3e)
の`src/types/ble.ts`はPresence=`06`とし、`src/ble.ts`は先頭byteの下位7 bitでmodelを選ぶ。
`src/devices/wo-presence.ts`も`00 20`の複合識別規則を提供していない。
この差だけではfirmware / broadcast modeの対応関係を証明できないため、`06`を追加せず、
既存`70`対応は参照実装由来・実機未確認として残す。

**判断B: 自動識別の根拠は不足。** 未知機器でも満たせる長さ・正常値・実測固定値を
組み合わせて`presence_legacy_observed`として確定することはしない。

### Presenceの未確認候補・明示登録方式

- 自動decoderの結果は`unknown_switchbot`のまま。candidate本体の`model`と`values`は
  unknown／空のままにする。正常センサの測定値ではない。
- serviceが空の12-byte manufacturer-only形式、または`00 20` service付きで既存Presence
  decoderを検証できる形式に限り、candidate APIに
  `manual_registration_models: ["presence_sensor"]`と`unconfirmed_preview`を付ける。
  これは識別結果ではなく登録選択肢。
- Dashboardで「Presence Sensor Pro と確認して登録」を選び、実物と対象候補を照合したことを未選択の
  checkboxで明示確認する。一般のunknownや他機種を任意modelへ変更するUIは作らない。
- 登録requestの`confirmed_model: "presence_sensor"`を利用者の機種指定として受け付ける。
  ID提案はregistryを変更しない。登録時には最新candidateのrawを再検証し、不正・未知type・
  明示typeとの矛盾・重複serviceは拒否する。登録済みidentityの重複は既存registryが拒否する。
- 検証に成功した場合だけ既存schemaでphysical keyに`presence_sensor`を保存する。
  確認直後のraw evidenceは`user_confirmed_presence_sensor`、再受信時は`registered_hint`になる。
  確認evidenceを永続保存するschema追加は行わない。
- 登録後も同じvalidationを通す。service付きではbattery/status/lightを返し、serviceなしでは
  status/lightだけを返す。M1の優先順位も不変。
- この形式を検証できたcandidateにだけ、`unconfirmed_preview.values`を付ける。Dashboardは
  「未確認プレビュー」「Presence Sensor Proとして解釈した参考値」
  と表示し、RSSI、最終受信時刻、identifier suffixとともに実物の操作との連動確認に使う。
  previewはhint、sensor ID、registered sensor、MQTT通常topic、Parquet・研究データ保存へ
  渡さない。packet更新時にはpreviewだけを更新し、malformed・矛盾・他機種のpacketでは消す。
- Dashboardはsetup scan中にcandidate APIを5秒ごとに`no-store`で再取得する。同じphysical
  keyの最新snapshotで候補カードを描き直すため、preview、RSSI、最終受信時刻、highlightも更新
  される。登録dialogは候補一覧の外にあるため、checkboxなどの入力状態は再描画しない。
  candidateの保持・scan終了時の既存ルールは変更しない。最終受信時刻を現在値と誤解しない。

### Motionの機種識別と測定値

- service type `0x73`はMotion Sensorの明示的な**機種識別**根拠である。未登録のmanufacturer-only packetは自動判定ではunknownのままとする。既存decoderで各modelとして再検証し、妥当な候補がちょうど1種類の場合だけ、利用者確認付きの「SwitchBot 人感センサー候補（未確認）」として登録できる。防水温湿度計等と同じ安全設計で、複数modelが成立する場合や妥当性を確認できない場合は登録選択肢を出さない。登録時も最新packetを再検証し、確認前のpreviewを通常の計測値として保存・送信しない。
- manufacturer 10-byte statusは機種識別根拠ではない。OMK実機ではserviceが約40秒
  `73 80 64 00 dd 0a`のままでもmanufacturer statusが`2c`から`6c`へ変化し、実物の
  検知と一致した。このstatus bit 6を**測定値**として使用する。
- Motionとしてserviceまたは登録hintで選択済みなら、妥当なmanufacturer statusをserviceの
  `motion_state`より優先する。両fieldは同じ時点のsnapshotとは限らないため、矛盾をunknownに
  しない。manufacturerが不正なら、妥当な`0x73` serviceの値へfallbackする。
- 登録済みMotionのmanufacturer-only受信は、既存hintとmanufacturer layout validationで継続復号する。

### Meter manufacturer-only形式の初回登録

- `0x54`（Meter）と`0x69`（Meter Plus）のservice typeは、従来どおり共有内部model
  `temperature_humidity_sensor`の明示的な機種識別根拠である。manufacturer-only layoutから
  MeterとMeter Plusを区別する根拠はない。
- 利用者の実機では登録hintを削除した状態でservice data・local name・service UUIDが取得されず、
  20秒のBleak scanでもmanufacturer dataだけを観測した。既存Meter decoderはそのpayloadを
  本体表示と同じ24.0 ℃ / 52 %へ復号できた。ただしこれは値形式の単一実測であり、manufacturer
  layoutをMeter固有のmodel discriminatorへ格上げする根拠にはならない。
- service dataが空で、既存Meter manufacturer decoderの完全なlayout validationと温度・湿度範囲
  validationの両方に通る未登録candidateだけに、`temperature_humidity_sensor`の明示登録選択肢を
  付ける。candidate本体は引き続き`unknown_switchbot`で、previewは「SwitchBot 温湿度計候補
  （未確認）」として表示する。Meter / Meter Plusの製品名はpreviewで断定しない。
- Dashboardの参考値は温度、相対湿度、RSSI、最終受信時刻、identifier suffixである。正常な値は
  Meterを証明しないため、利用者が本体液晶や温度変化との連動を確認してから「SwitchBot 温湿度計と
  確認して登録」を選ぶ。
- serviceが一つでも存在する、layoutまたは値が不正、あるいはPresenceを含む複数の未確認形式に
  一致する場合は、Meter候補を出さない。登録時は最新packetを同じ条件で再検証し、成功時だけhintを
  保存する。previewはMQTT通常topic、保存・集約、sensor ID、registered sensorへ渡さない。

### CO₂ manufacturer-only形式の初回登録

- `0x35` service typeはCO₂センサーの明示的な機種識別根拠であり、従来どおり未登録個体を
  自動識別する。serviceが空の16-byte manufacturer layoutは、decoderが復号できてもmodelを
  自動選択しない。
- 利用者は2台目の未登録個体で`b0e9fe5815cc1ae405992d003b02fe00`を観測し、既存decoderの
  766 ppm / 25.5 ℃ / 45 %が本体表示と一致すると報告した。これはCO₂値のlayout解釈を補強する
  B相当の実測であり、16-byte長、末尾`00`、正常値、可変byteを組み合わせても機種固有の
  discriminatorにはならない。
- service dataが空で、既存CO₂ decoderの16-byte長、末尾terminator、Meter互換温湿度
  expression、温度・湿度・CO₂の値範囲をすべて検証できる未登録candidateだけに
  `co2_sensor`の明示登録選択肢を付ける。candidate本体は`unknown_switchbot`のままとし、
  Dashboardは「SwitchBot CO₂センサー候補（未確認）」でCO₂濃度・温度・相対湿度を参考値として
  表示する。
- 利用者は本体液晶や値変化との連動を確認してから「SwitchBot CO₂センサーと確認して登録」を
  選ぶ。登録時は最新packetを再検証し、成功時だけ`co2_sensor` hintを保存する。明示serviceが
  別model、layoutまたは値が不正、あるいは複数の未確認形式に一致する場合は候補を出さない。
  previewはMQTT通常topic、保存・集約、sensor ID、registered sensorへ渡さない。

ここでの根拠は「利用者が実物の機種を確認したこと」。誤った候補を選べば誤登録の余地は
残るため、正常値が得られたことだけで確認済みとしない。BLE identityの認証を追加したものではない。

### 利用者による再試験

1. 対象Presenceの登録を削除し、BLE managerを再起動する。
2. セットアップscanを開始する。`00 20` service付きまたはmanufacturer-only候補は**自動ではunknownのまま**で、
   「Presence Sensor Pro候補（未確認）」と「未確認プレビュー」が表示されることを確認する。
3. 実物の前を動く、周囲の明るさを変えるなどして、previewの検知・照度（service付きならbatteryも）と
   RSSI・最終受信時刻・identifier suffixが対象カードで連動することを確認する。
4. 対象実物を照合し、checkboxを選択して登録する。
5. 在／不在・照度（service付きならbatteryも）の変化、Dashboard・MQTTの値を確認する。
6. BLE manager再起動後も同じphysical keyと登録IDで継続することを確認する。

従来の再試験ゴールの「未登録時にPresenceとして自動認識」は採用しない。
エージェントの自動テストでは、登録前拒否・明示登録・registry再読込・値取得まで確認した。
公開前の追加実機報告では、新品microSDから構築した標準Pi4 Gatewayで、旧registryなしのPresence Sensor Pro初回登録が確認された。登録時packetの形式や自動判定／明示選択の別までは、この報告から断定しない。過去にPi内蔵BluetoothでScan Response/service dataを取得できなかった観測は、環境差として保持する。

### 別件の作業メモ（今回未修正）

利用者の実機試験で、登録済みOMK Node 3台がDashboard「未登録デバイス」とcandidate APIに
現れるとの報告あり。Node候補除外の問題として別途調査する。今回のPresence/M1/M2変更に
Nodeの候補生成・登録状態処理の修正は含めていない。
