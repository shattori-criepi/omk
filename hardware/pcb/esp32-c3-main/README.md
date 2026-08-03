# OMK ESP32-C3 MAIN Rev.A — Phase 2 schematic

KiCad 9.0.8用のメイン基板回路図である。PCBレイアウト、BOM/CPL、Gerberはまだ作成しない。
回路図はA3横向き1枚に機能ブロックごとに整理し、右下のタイトルブロック領域を予約している。
メーカー型番、LCSC番号、Footprint、選定メモはシンボルプロパティとして保持するが、PDFには
Referenceと簡潔なValue以外を表示しない。ESP32-C3のアンテナkeepoutはPCB Phaseで確認する。

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
公式Espressifライブラリは `symbols/Espressif.kicad_sym`、公式footprintは
`footprints/Espressif.pretty/` に同梱し、それぞれのプロジェクト表は
`sym-lib-table` / `fp-lib-table` に登録する。生成APIとの互換性のため、シンボルは公式リポジトリの
`legacy_kicad7`ブランチ版を用いる（現在版は `Espressif-kicad9.kicad_sym` として保存）。

## 回路図ブロック

| ブロック | 実装内容 |
| --- | --- |
| USB-C | J1、VBUS PTC F1、CC1/CC2の5.1 kΩ Rd、USBLC6-2SC6 ESD、入力10 µF/100 nF、SBU NC、R3 0 Ω DNP shield option |
| 電源 | AP63203WU-7（3.3 V/2 A候補）、BST C3、4.7 µH L1、出力22 µF×2。L1品番は未確定。 |
| MCU | ESP32-C3-MINI-1-H4X、3V3デカップリング、GPIO18/19 USB、GPIO6/7 I2C、GPIO9 BOOT、GPIO20/21 UART。NC/GND padを明示。 |
| BOOT/RESET | ENの10 kΩ pull-up、1 µF、RESETスイッチ、GPIO9の10 kΩ pull-up、BOOTスイッチ。 |
| I2C/外部 | 4.7 kΩ DNP pull-up、SEN66 1×4 J3、Qwiic JST SH J2、SDA/SCL TP。 |
| 評価用 | UART、+5V、+3V3、GND、USB、EN、BOOTのTPとGPIO3 status LED（DNP）。 |

## 主要部品

| Ref | MPN / 候補 | LCSC | Footprint | 状態 |
| --- | --- | --- | --- | --- |
| U1 | Espressif ESP32-C3-MINI-1-H4X | C41349510 | `Espressif:ESP32-C3-MINI-1` | 採用候補。JLC実装可否・在庫は発注時確認。 |
| U2 | Diodes AP63203WU-7 | C780769 | `Package_TO_SOT_SMD:TSOT-23-6` | 3.3 V/2 A buck候補。参照回路・在庫を最終照合。 |
| J1 | JAE DX07S016JA1R1500 | TBD | `Connector_USB:USB_C_Receptacle_JAE_DX07S016JA1R1500` | USB2.0 sink候補。 |
| D1 | ST USBLC6-2SC6 | C7519 | `Package_TO_SOT_SMD:SOT-23-6` | USB ESD候補。 |
| J2 | JST SM04B-SRSS-TB(LF)(SN) | C160404 | `Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal` | Qwiic。 |
| J3 | 2.54 mm 1×4 RA socket | TBD | `Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Horizontal` | 手実装候補。品番未確定。 |
| L1 | 4.7 µH、Isat ≥2.5 A | TBD | `Inductor_SMD:L_Vishay_IHLP-2020` | 実部品・飽和電流未確定。 |

R3、R6/R7、R8/D2はDNP選択肢である。USB shieldのR3実装、I2C pull-up実装、LED実装は実機レビュー後に決定する。

## ERC

正式回路図のKiCad CLI ERCは **0 errors / 1 warning**。警告は埋め込み済みU1シンボルとプロジェクト内の
公式Espressifライブラリ版の比較差異（`lib_symbol_mismatch`）だけで、全ピン番号は生成前検証済みである。
`kicad-sch-api`が現行KiCad 9/10形式の公式ライブラリを正しく埋め込めないため、公式KiCad 7互換版を使用したことに由来する。
意図的に残し、GUI目視確認時にU1の53ピンとfootprint対応を再照合する。`erc-report.txt`を参照。

## Phase 3へ進む前の確認

- U2/L1/L/CをAP63203WU-7の最新データシート推奨値、発熱、JLC在庫で確定する。
- J1、D1、J2、U1の現時点のJLC在庫・PCBA可否・CPL回転を確認する。
- U1のアンテナkeepout、露出GND pad/thermal via、USB差動対、buck SWノードをPCBレビューする。
- USB shieldの筐体/FG方針、J3の逆挿し対策、DNP部品の実装方針を承認する。
- 実機でUSB CDC、書込み、BOOT、SEN66、Wi-Fi/MQTT、電源リップルを評価する。
