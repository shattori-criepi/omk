# OMK ESP32-C3 MAIN Rev.A — CANCELLED reference design

> **Status: CANCELLED — NOT FOR FABRICATION**
>
> この一体型PCB計画は2026-08-04に正式中止された。回路図、PCB、footprint、生成スクリプトおよび監査資料は履歴・参考資料として保持するが、電気的・物理的に検証済みではなく、製造や発注に使用してはならない。後継構成は未決定である。決定記録は[ESP32-C3＋SEN66一体型PCB Rev.A 計画中止](../../../docs/decisions/esp32-c3-integrated-pcb-cancellation.md)を参照。

以下は中止時点のKiCad 9.0.8メイン基板回路図に関する履歴記録である。PCBレイアウト、BOM/CPL、Gerberは未完了であり、今後の作業計画を示すものではない。
回路図はA3横向き1枚に機能ブロックごとに整理し、右下のタイトルブロック領域を予約している。
メーカー型番、LCSC番号、Footprint、選定メモはシンボルプロパティとして保持するが、PDFには
Referenceと簡潔なValue以外を表示しない。ESP32-C3のアンテナkeepoutはPCB Phaseで確認する。
電源の現行仕様は AP63203WU-7 / C780769、C1=10uF、C2=100nF、C3=100nF BST–SW、
L1=4.7uH、C4/C5=22uF×2である。L1の電気的MPNはCoilcraft XGL4020-472MECへ選定済みで、
2D footprint `Inductor_SMD:L_Coilcraft_XxL4020` とMPN/LCSCはgenerator・正式回路図へ反映済みである。
3D/物理向き、JLC調達は未完了であり、C1/C4/C5のMLCC DCバイアスとともに製造リリースBLOCKERである。
詳細は [power-review.md](power-review.md) を正とする。R6/R7は4.7kΩ DNPで、キャリアR1/R2も同じDNP選択肢である。

## 再生成と検証

空テンプレート `esp32-c3-main.blank.kicad_sch` を
`kicad-sch-api==0.5.6` で読み込んで編集する。`.kicad_sch` を文字列置換していない。
生成前に標準ライブラリおよび公式Espressifシンボルのピン集合を検証し、すべての部品を0°で配置する。

```bash
KICAD_SYMBOL_DIR=/usr/share/kicad/symbols \
  .venv/bin/python scripts/generate_schematic.py
kicad-cli sch erc esp32-c3-main.kicad_sch --output erc-report.txt --severity-all
kicad-cli sch export pdf esp32-c3-main.kicad_sch \
  --output esp32-c3-main-schematic.pdf
```

生成中間物は `esp32-c3-main.generated.kicad_sch`。ERCにエラーがあれば、スクリプトは正式ファイルへ反映しない。
Git管理するEspressifシンボルは `symbols/Espressif.kicad_sym`、公式footprintは
`footprints/Espressif.pretty/` に同梱し、それぞれのプロジェクト表は
`sym-lib-table` / `fp-lib-table` に登録する。生成APIとの互換性のため、シンボルは公式リポジトリの
`legacy_kicad7`互換版である。

## 回路図ブロック

| ブロック | 実装内容 |
| --- | --- |
| USB-C | J1、VBUS PTC F1、CC1/CC2の5.1 kΩ Rd、USBLC6-2SC6 ESD、入力10 µF/100 nF、SBU NC、R3 0 Ω DNP shield option |
| 電源 | AP63203WU-7（3.3 V/2 A）、BST C3、4.7 µH L1、出力22 µF×2。L1はXGL4020-472MEC / C6012418と`Inductor_SMD:L_Coilcraft_XxL4020`をgenerator・正式回路図へ統合済み。3D・調達は未完了。 |
| MCU | ESP32-C3-MINI-1-H4X、3V3デカップリング、GPIO18/19 USB、GPIO6/7 I2C、GPIO9 BOOT、GPIO20/21 UART。NC/GND padを明示。 |
| BOOT/RESET | ENの10 kΩ pull-up、1 µF、RESETスイッチ、GPIO9の10 kΩ pull-up、BOOTスイッチ。 |
| I2C/外部 | 4.7 kΩ DNP pull-up、SEN66 1×4 J3、Qwiic JST SH J2、SDA/SCL TP。 |
| 評価用 | UART、+5V、+3V3、GND、USB、EN、BOOTのTPとGPIO3 status LED（DNP）。 |

## 主要部品

| Ref | MPN / 候補 | LCSC | Footprint | 状態 |
| --- | --- | --- | --- | --- |
| U1 | Espressif ESP32-C3-MINI-1-H4X | C41349510 | `Espressif:ESP32-C3-MINI-1` | 採用候補。JLC実装可否・在庫は発注時確認。 |
| U2 | Diodes AP63203WU-7 | C780769 | `Package_TO_SOT_SMD:TSOT-23-6` | 3.3 V/2 A buck候補。参照回路・在庫を最終照合。 |
| J1 | JAE DX07S016JA1R1500 | C3197885（候補、回路図propertyは未変更） | `Connector_USB:USB_C_Receptacle_JAE_DX07S016JA1R1500` | 中止前の評価はElectrical ACCEPT、JAE `SJ121837`のland/board-edge照合、3D/機構、調達はVERIFY。現行の製造リリース状態はCANCELLED。 |
| D1 | ST USBLC6-2SC6 | C7519 | `Package_TO_SOT_SMD:SOT-23-6` | USB ESD候補。 |
| J2 | JST SM04B-SRSS-TB(LF)(SN) | C160404 | `Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal` | Qwiic。 |
| J3 | JST S4B-PH-K-S(LF)(SN) | C157926 | `Connector_JST:JST_PH_S4B-PH-K_1x04_P2.00mm_Horizontal` | キャリアJ2と同一変更セットで統合済み。物理向き/3D/mechanicalはPhase 3B VERIFY。 |
| L1 | Coilcraft XGL4020-472MEC | C6012418（発注時VERIFY） | `Inductor_SMD:L_Coilcraft_XxL4020`統合済み。旧`Inductor_SMD:L_Vishay_IHLP-2020`は不採用。 | Electrical/2D footprint ACCEPT・統合済み、3D/調達VERIFY、Release BLOCKER。 |

R3、R6/R7、R8/D2はDNP選択肢である。USB shieldのR3実装、I2C pull-up実装、LED実装は実機レビュー後に決定する。

## ERC

正式回路図のKiCad CLI ERCは **0 errors / 2 warnings**。警告はいずれもU1のプロジェクト内
Espressif symbol/footprint library設定に関するもので、J3統合による警告はない。全ピン番号は生成前検証済みである。
`kicad-sch-api`が現行KiCad 9/10形式の公式ライブラリを正しく埋め込めないため、公式KiCad 7互換版を使用したことに由来する。
意図的に残し、GUI目視確認時にU1の53ピンとfootprint対応を再照合する。`erc-report.txt`を参照。

## 中止時点の未完了確認事項（履歴）

以下は完了しておらず、計画中止により終了した項目である。後継設計で自動的に引き継がない。

- L1のJLC調達、3D/short-start lead向き確認、C1/C4/C5の公式DC-bias曲線、AP63203 EVMとのpin-by-pin照合を完了する。
- J1のJAE `SJ121837`、`JACS-30413`、`JAHL-30353-1`を取得し、land、shell/NPTH、board-edge datum、mating/rework clearanceを `usb-c-j1-footprint-audit.md` と照合する。J1、D1、J2、U1の現時点のJLC在庫・PCBA可否・CPL回転も確認する。
- U1のアンテナkeepout、露出GND pad/thermal via、USB差動対、buck SWノードをPCBレビューする。
- USB shieldの筐体/FG方針、J3の逆挿し対策、DNP部品の実装方針を承認する。
- J3/J2は裸2.54 mm案からJST `S4B-PH-K-S(LF)(SN)` / C157926へ双方同時変更済み。pin 1=3V3、2=GND、3=SDA、4=SCLを維持し、再生成/ERC/PDF確認済み。PCB上の回転・ハーネス外形・3D/mechanical確認はPhase 3Bで行う（詳細は `../jst-ph-header-selection.md`）。
- 回路図へ反映する変更順序とPhase 3Bゲートは [`../phase-3a-release-review.md`](../phase-3a-release-review.md) を正とする。
- 実機でUSB CDC、書込み、BOOT、SEN66、Wi-Fi/MQTT、電源リップルを評価する。
