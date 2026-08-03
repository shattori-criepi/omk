# OMK ESP32-C3 / SEN66 PCB Rev.A — Phase 1

更新日: 2026-08-03。これは設計指示書に対する Phase 1（調査・基本設計）の成果物である。KiCad 回路図、PCB、Gerber、BOM/CPL は Phase 2 以降に作成する。ここでの「候補」は発注承認前に固定しない。

## 結論

Rev.A は `ESP32-C3-MINI-1-H4X`（LCSC `C41349510`）を表面実装する。4 MB 内蔵フラッシュ、RISC-V、内蔵 PCB アンテナおよび USB Serial/JTAG を備える公式モジュールであり、USB D-=`GPIO18`、D+=`GPIO19` とする。`ESP32-C3-MINI-1-N4` / `-H4` は設計指示どおり採用しない。N4X は H4X と同一の電源・フットプリントを再照合した場合だけ代替候補とする。

公式資料では GPIO2/8/9 がストラップで、通常起動には GPIO9 のプルアップ、download には GPIO9 Low が必要である。GPIO6/7 は JTAG の既定端子でもあるため、I2C に使うことで外部 JTAG は提供しないが、USB Serial/JTAG は維持される。GPIO6/7 は起動ストラップではなく、SEN66 と Qwiic の 100 kHz I2C に適する。

## 一次資料と発注前確認

- [ESP32-C3-MINI-1 datasheet](https://documentation.espressif.com/esp32-c3-mini-1_datasheet_en.html): H4X を含むシリーズ、推奨ランドパターン、アンテナ配置、4 MB flash を確認する。
- [Espressif C3 schematic checklist](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32c3/schematic-checklist.html): GPIO9 pull-up、USB GPIO18/19、EN/ストラップ回路を最終回路図で照合する。
- [SEN6x datasheet](https://sensirion.com/media/documents/FAFC548D/693FBB15/PS_DS_SEN6x.pdf): VDD=3.15–3.6 V、SEN66 measurement は typ. 90 mA / max. 110 mA、2 ms peak max. 350 mA、I2C address `0x6B` / max. 100 kbit/s、Pin 1/6=VDD、2/5=GND、3=SDA、4=SCL を確認した。
- [SEN6x mechanical guideline](https://sensirion.com/media/documents/EA641247/6977159F/PS_AN_SEN6x_Mechanical_Design_and_Assembly_Guidelines_D1.pdf): 外部熱源から離し、温度上昇を 5 K 未満に抑える設計を要求している。
- [JLCPCB BOM/CPL guidance](https://jlcpcb.com/help/article/advice-for-bom-and-cpl-files-preparation): 発注時に BOM/CPL の designator 一致、単位、表裏、部品照合を行う。

この環境から JLCPCB のログイン済み在庫・PCBA見積・事前購入状態にはアクセスできないため、`C41349510` の**現在在庫、単価、Standard/Economic 可否、最小数、X線/事前購入要否は未確認**である。発注画面で H4X を指定して再確認し、その結果を BOM と発注記録へ転記する。PCBA 可否をこの時点で断定しない。

## ファームウェア互換性とビルド

既存プロジェクトは PlatformIO `espressif32@6.9.0`、Arduino、Sensirion I2C SEN66 `1.3.1`、Sensirion Core `0.7.3`、PubSubClient `2.8` を使用する。Wi-Fi は Arduino `WiFi.h`、MQTT は PubSubClient、JSON は固定長 `snprintf` 系（ArduinoJson 不使用）である。OTA、ESP32-WROOM 固有 API、外部 USB-UART 依存はない。Wi-Fi/MQTT/SEN66 の再接続・再初期化は実装済みである。

`platformio.ini` に `omk-esp32-c3` を追加した。これは `esp32-c3-devkitm-1` を C3-MINI-1 の PlatformIO 互換ターゲットとして使い、4 MB / DIO、I2C GPIO6/7 を指定する。C3 の内蔵 USB Serial/JTAG で `Serial` を使うため、C3環境だけに `ARDUINO_USB_MODE=1` と `ARDUINO_USB_CDC_ON_BOOT=1` を設定する。既存 `esp32dev` は従来のUART Serial構成のまま維持し、これらの定義は追加しない。

`Serial`、`millis()`、`delay()` などの Arduino API を直接使う実装ファイルは、間接includeに依存しない。`main.cpp` は既に `Arduino.h` を明示includeしており、今回 `mqtt_publisher.cpp` と `sen66_sensor.cpp` にも同じincludeを追加した。

### 実機前ビルド確認（成功）

| PlatformIO環境 | 結果 | RAM | Flash |
| --- | --- | --- | --- |
| `omk-esp32-c3` | SUCCESS | 39,604 / 327,680 bytes (12.1%) | 756,128 / 1,310,720 bytes (57.7%) |
| `esp32dev` | SUCCESS | 45,616 / 327,680 bytes (13.9%) | 773,377 / 1,310,720 bytes (59.0%) |

C3用USB CDC定義と明示includeを含めて C3 環境のビルドは成功した。既存 `esp32dev` も回帰ビルドに成功しており、設定・UART Serial動作を変更していない。実機基板は未完成のため、USB書き込み、SEN66通信、Wi-Fi接続、MQTT接続の実動作確認は未実施である。手動 BOOT、USB CDC、24時間連続運転も Rev.A 受領後の評価項目とする。

## GPIO と信号表

| 信号 | GPIO / net | 方針 |
| --- | --- | --- |
| SEN66/Qwiic SDA | GPIO6 | 3V3へ 10 kΩ、SJで切離し可能 |
| SEN66/Qwiic SCL | GPIO7 | 3V3へ 10 kΩ、SJで切離し可能 |
| USB D- / D+ | GPIO18 / GPIO19 | USB-Cから ESD を経て短い差動配線。長いTPは置かない |
| BOOT | GPIO9 | 10 kΩ pull-up、押下で GND。TPあり。大容量Cを置かない |
| RESET | EN | 10 kΩ pull-up、1 µF to GND、押下で GND、TPあり |
| UART RX / TX | GPIO20 / GPIO21 | 3.3 V TTL TPのみ。通常ログ/書込みは native USB |
| Status LED | GPIO3（DNP） | 直列抵抗とともに DNP。GPIO2/8/9、USB端子を避ける |

GPIO2 は 10 kΩ pull-up を置き未使用、GPIO8 は未接続、GPIO9 は上記の download 回路とする。SEN66/Qwiic 接続機器が起動時のストラップへ影響しないよう、I2C は GPIO6/7 のみへ接続する。

## Phase 2: 回路図設計（KiCad転記仕様）

編集可能なKiCad回路図は、KiCad 9環境でこの仕様を回路図化し、ERCを通して確定する。未検証のKiCad S式ファイルを手書きで生成して成果物と見なさない。プロジェクト配置と回路図ネット表は `hardware/pcb/` に置く。

USB-C入力部については、この後にKiCad 9.0.8と `kicad-sch-api==0.5.6` を用いるPoCを実施した。KiCad 9作成の空テンプレートをAPIで読み込み、標準シンボルのピン集合を照合してから `J1`、`F1`、`R1`/`R2`、`R3`（DNP shield option）、`D1`、`C1`/`C2`、GND/PWR_FLAG、USBネットラベル、SBU No Connectを生成した。全シンボルは0°配置である。`kicad-cli sch erc` は **0 errors / 0 warnings**、PDF出力も成功した。生成中間物と正式回路図、ERC report、PDFは `hardware/pcb/esp32-c3-main/` にある。

### メイン基板ネット表

#### Phase 2 実装結果（メイン基板のみ）

KiCad 9.0.8 の空テンプレートを `kicad-sch-api==0.5.6` で読み込み、USB-C、VBUS保護、
AP63203WU-7（3.3 V/2 A候補）の降圧電源、ESP32-C3-MINI-1-H4X、EN/BOOT、I2C、
SEN66用J3、Qwiic用J2、UART/電源/USB/制御用TP、DNP status LEDを
`hardware/pcb/esp32-c3-main/esp32-c3-main.kicad_sch` へ生成した。ESP32-C3の公式シンボル、
フットプリント、53ピン集合は公式Espressifライブラリとデータシートに照合した。

I2CはGPIO6=SDA、GPIO7=SCL、USBはGPIO18=D-、GPIO19=D+、UART TPはGPIO20=RX、
GPIO21=TX、BOOTはGPIO9、status LED候補はGPIO3である。GPIO2、GPIO8、未使用GPIO、
公式指定NC端子はNo Connectとして明示した。USB shieldはR3（0 Ω DNP）でGNDへ接続可能な
選択肢を残した。

`kicad-cli sch erc` は正式回路図に対して **0 errors / 1 warning**、PDF出力は成功した。
残る警告は、`kicad-sch-api 0.5.6` と現行公式KiCad 9/10シンボル形式の互換性を回避するため、
公式Espressif KiCad 7互換ライブラリを使用した際の `lib_symbol_mismatch` のみである。
U1のピン集合は生成前に検証済みであり、Phase 3前のGUI目視レビューで再確認する。
ERC reportとPDFは `hardware/pcb/esp32-c3-main/` に置く。SEN66キャリア回路図とPCBレイアウトは
未着手である。

今回の候補は AP63203WU-7（LCSC `C780769`）、USBLC6-2SC6（`C7519`）、
JST SM04B-SRSS-TB(LF)(SN)（`C160404`）、ESP32-C3-MINI-1-H4X（`C41349510`）である。
U2周辺L1の実品番、USB-C J1、J3は未確定であり、JLC在庫・PCBA可否を断定していない。
TPS62162DSGRは1 A級のため、要求した1.5 A以上の電源余裕を満たす採用品にはしない。

| Ref | 接続 | 値・指定 | 注記 |
| --- | --- | --- | --- |
| J1 | USB-C receptacle: VBUS→F1→`+5V`; CC1/CC2→R1/R2→GND | R1/R2 5.1 kΩ | USB 2.0 sink専用。SBU/高速信号はNC。 |
| D1 | J1 D+/D- と U1 GPIO19/18 の間 | USB 2.0 2ch ESD | D+はGPIO19、D-はGPIO18。ESDのGNDを最短でGND面へ。 |
| U2 | `+5V`→`+3V3` | 1 A以上 buck（候補 TPS62162DSGR） | VIN/VOUT/L/Cは**最終選定ICの参照回路どおり**に配置。 |
| U1 | `+3V3`, GND, EN, GPIO6/7/9/18/19/20/21 | ESP32-C3-MINI-1-H4X | 公式footprint、底面GND pad/thermal via、アンテナkeepout。 |
| R3/C1/SW1 | EN→R3→`+3V3`; EN→C1→GND; SW1: EN→GND | R3 10 kΩ, C1 1 µF | RESET。C1はU1近傍。 |
| R4/SW2 | GPIO9→R4→`+3V3`; SW2: GPIO9→GND | R4 10 kΩ | BOOT。GPIO9に大容量コンデンサを置かない。 |
| R5/R6/SJ1/SJ2 | GPIO6/7→SJ→R→`+3V3` | 10 kΩ 1%、SJ切離し可 | SDA/SCLプルアップはメインのみ。 |
| J2 | GND, `+3V3`, SDA, SCL | JST SH 1×4 Qwiic | Qwiic公式ピン順をfootprintと実コネクタで最終照合。 |
| J3 | `+3V3`, GND, SDA, SCL | 2.54 mm 1×4 RA socket | キャリアへ。Pin 1を`+3V3`とする。 |
| TP1..TP11 | `+5V`,`+3V3`,GND,SDA,SCL,D+,D-,EN,BOOT,U0TXD,U0RXD | test point | D+/D-はESD/U1寄りに小パッド、長いstub禁止。 |
| TP10/TP11 | GPIO21/GPIO20 | UART TX/RX | 3.3 V TTL専用。USB CDCとは別。 |
| D2/R7 | GPIO3→R7→D2→GND | DNP, 1 kΩ | 任意status LED。 |

U1のGPIO2は10 kΩ pull-up、GPIO8はNCとする。NC端子はKiCadで `No connect` フラグを付ける。USBのCC、ESD、DC-DC、ESP32、SEN66コネクタは電源/信号が同名ネットで接続されることをERCで確認する。

### SEN66キャリア基板ネット表

| Ref | 接続 | 値・指定 | 注記 |
| --- | --- | --- | --- |
| J1 | `+3V3`, GND, SDA, SCL | 2.54 mm 1×4 RA header | メインJ3とPin 1=`+3V3`を一致。 |
| H1..H6 | H1/H6→`+3V3`; H2/H5→GND; H3→SDA; H4→SCL | 6本のケーブル直接はんだ穴 | SEN66 Pin 1から導通確認する。H1=VDD、H2=GND、H3=SDA、H4=SCL、H5=GND、H6=VDD。 |
| C1/C2 | `+3V3`→GND | 47 µF bulk / 100 nF ceramic | ケーブル入口・SEN66電源近傍。キャリアにI2C pull-upは置かない。 |
| TP1..TP4 | `+3V3`, GND, SDA, SCL | test point（任意） | 手実装/評価用。 |

ケーブル穴のシルクには `1 VDD`, `2 GND`, `3 SDA`, `4 SCL`, `5 GND`, `6 VDD`, `PIN 1`, `TO SEN66` を明記する。保持具・M3穴・ストレインリリーフはPCB Phaseで機構レイヤに配置し、電気回路には実装穴として記載する。

## 電源方針

設計負荷は SEN66 350 mA peak + ESP32-C3 Wi-Fi送信時 350 mA（設計マージン値）+ Qwiic 100 mA + LED/損失 50 mA = **850 mA** とする。よって 3V3 は連続 1 A 級、短時間余裕を含み 1.5 A 級を選定する。

| 方式 | 判定 | 理由 |
| --- | --- | --- |
| 1 A以上 LDO | 不採用 | 5 V→3.3 Vで 0.85 A 時に約1.45 Wを熱にする。SEN66温度へ不利。 |
| 降圧 DC-DC | Rev.A採用予定 | 90%級なら上記設計負荷で損失は約0.3 W。部品点数とEMIを配慮してメイン基板のSEN66接続端と反対側へ置く。 |

採用候補は TI `TPS62162DSGR`（1 A buck、代替: Diodes `AP63203WU` 2 A）。最終選定は各データシートの推奨 L/C、実負荷過渡、JLCの在庫・PCBA区分を満たすものに限る。5 V入力には polyfuse（候補 1 A hold）、TVS/ESD、10 µF + 100 nF、3V3 出力には regulator 指定値に加えメイン基板 22 µF。キャリアのケーブル入口には 47 µF + 100 nF を SEN66 VDD/GND 直近に置く。SEN66 の 30 mVpp（100 Hz未満）要求は評価でオシロスコープ測定する。

## 概略回路

```text
USB-C ─ fuse/TVS ─ 5V TP ─ buck 3V3 ── ESP32-C3-MINI-1-H4X
  CC1/CC2: 5.1k to GND                 ├─ I2C pull-up SJ+10k ─ Qwiic
  D+/D-: ESD → GPIO19/18               └─ 1x4 board connector → carrier
BOOT: GPIO9 pull-up / switch to GND; RESET: EN RC / switch to GND

carrier: 1x4 (3V3,GND,SDA,SCL) ─ 47u+0.1u ─ 6-wire cable pads
         SEN66 pin 1+6=3V3, 2+5=GND, 3=SDA, 4=SCL
```

USB-C は sink 専用（CC1/CC2 とも 5.1 kΩ Rd）、VBUS保護、シールドのシャーシ/GND接続方針を回路レビューで固定する。USB-C receptacle、ESD、buck、ESP32、SMD SW、Qwiic は PCBA候補、1x4ライトアングル、TP、SEN66ケーブルは手実装候補とする。

## レイアウト・機構案

- メイン基板: およそ **45 × 35 mm**、USB-C と DC-DC を一辺、ESP32アンテナを反対側の板端から外向きに置く。アンテナ下/前方は全層銅、ビア、部品、ケーブル、M3、キャリアを禁止する。M3穴は2か所以上、銅 keepout付き。
- キャリア: およそ **65 × 35 mm**、SEN66の吸排気に重ならない平坦な保持領域、M3穴、結束バンド用スリットを備える。SEN66は交換可能なアクリル/3Dプリントretainer、面ファスナーまたは結束バンドで保持する。STEPで干渉確認するまで外形は暫定。
- 二枚は横並び・同一平面、各々をM3スペーサでベースへ固定する。熱源側をSEN66より上に置かず、キャリア接続端を電源回路と反対側にする。
- SEN66ケーブル完成長は **40 mm（30–50 mm許容）**。公式の短い接続推奨に従い、Pin 1から導通確認してからはんだ付ける。色だけで配線しない。

基板間コネクタは、要求どおり 2.54 mm 1×4ライトアングル（キャリア: pin header、メイン: socket）を基本案として**承認待ち**とする。安価、入手性、手直し性が利点だが、逆挿し防止がない。端部、Pin 1三角、表裏ネット名、非対称外形で誤接続リスクを下げる。極性付きJST案は確実だが、手直し性・面積・コストで不利なため、ユーザー承認なく変更しない。

## 採用予定部品

| 機能 | 候補 | 代替 | 実装 |
| --- | --- | --- | --- |
| MCU | ESP32-C3-MINI-1-H4X / C41349510 | ESP32-C3-MINI-1-N4X（承認・再照合必須） | JLC PCBA候補 |
| 3V3 buck | TPS62162DSGR | AP63203WU | JLC PCBA候補 |
| USB-C | 16-pin USB2.0 receptacle | 同フットプリント品 | JLC PCBA候補 |
| USB ESD | USBLC6-2SC6級 | PESD5V0S2UT級 | JLC PCBA候補 |
| Qwiic | JST SH 1×4 SM04B-SRSS-TB | 互換SH 1×4 | JLC PCBA候補 |
| I2C pull-up | 10 kΩ 1% 0402 + SJ | 4.7 kΩ（バス評価後） | JLC PCBA候補 |
| board link | 2.54 mm 1×4 RA header/socket | 極性付きJST | 手はんだ |

## リスク、未確定、次フェーズのゲート

1. H4X の発注時在庫/PCBA条件と全候補のLCSC番号・価格は未確認。見積画面で確認後にのみ BOM/CPL を確定する。
2. DC-DC のEMI/リップル、SEN66起動時電圧降下、温度オフセットは計算だけで合格にしない。Rev.A実測が必要。
3. SEN66 STEP、公式 retainer STEP、最新ハンドリング/温度補償資料をダウンロードし、キャリアの穴・空気流・干渉を3D確認する必要がある。
4. USB差動インピーダンス、アンテナkeepout、公式フットプリント/thermal via、ERC/DRC、CPL回転は KiCad Phase 2 で確認する。
5. 実機の USB CDC/upload、手動 BOOT、Wi-Fi/MQTT/SEN66、24時間連続試験は未実施。OTAは現行コードにないため、将来要件化するならパーティションとセキュリティ方針を先に決める。
