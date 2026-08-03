# OMK ESP32-C3 MAIN Rev.A — Phase 2 schematic input

このディレクトリはメイン基板のKiCad 9プロジェクトである。USB-C入力ブロックのPoC回路図を作成済みであり、PCBレイアウトには未着手である。

現時点の回路図転記元は [Phase 2ネット表](../../../docs/hardware/esp32-c3-sen66-pcb.md#phase-2-回路図設計kicad転記仕様) である。

## USB-C入力PoC

`scripts/generate_schematic.py` は、KiCad 9.0.8形式の空テンプレート
`esp32-c3-main.blank.kicad_sch` を `kicad-sch-api==0.5.6` で読み込み、
`esp32-c3-main.generated.kicad_sch` へ生成する。検証成功後、その内容を
`esp32-c3-main.kicad_sch` へ反映した。スクリプトはKiCadファイルの文字列置換をせず、
API経由で標準シンボルを検索、ピン番号を検証、配置、ネットラベル、配線を行う。

```bash
KICAD_SYMBOL_DIR=/usr/share/kicad/symbols \
  .venv/bin/python scripts/generate_schematic.py
kicad-cli sch erc esp32-c3-main.generated.kicad_sch \
  --output erc-report.txt --severity-all
kicad-cli sch export pdf esp32-c3-main.generated.kicad_sch \
  --output esp32-c3-main-schematic.pdf
```

PoCのERC結果は **0 errors, 0 warnings**。回路図PDFは
`esp32-c3-main-schematic.pdf`、生成中間物は
`esp32-c3-main.generated.kicad_sch` にある。

| Ref | KiCad標準シンボル | MPN | Footprint | 状態 |
| --- | --- | --- | --- | --- |
| J1 | `Connector:USB_C_Receptacle_USB2.0_16P` | JAE DX07S016JA1R1500 | `Connector_USB:USB_C_Receptacle_JAE_DX07S016JA1R1500` | 候補 |
| F1 | `Device:Polyfuse` | Bourns MF-MSMF110-2 | `Fuse:Fuse_1812_4532Metric` | 候補、要JLC照合 |
| R1/R2 | `Device:R` | Yageo RC0402FR-075K1L | `Resistor_SMD:R_0402_1005Metric` | CC Rd |
| R3 | `Device:R` | Yageo RC0402JR-070RL | `Resistor_SMD:R_0402_1005Metric` | DNP、shield方針未確定 |
| D1 | `Power_Protection:USBLC6-2SC6` | STMicroelectronics USBLC6-2SC6 | `Package_TO_SOT_SMD:SOT-23-6` | 候補 |
| C1 | `Device:C` | Murata GRM21BR61A106KE19L | `Capacitor_SMD:C_0805_2012Metric` | 10uF 10V X5R |
| C2 | `Device:C` | Murata GRM155R71E104KE14D | `Capacitor_SMD:C_0402_1005Metric` | 100nF 25V X7R |

`SBU1` / `SBU2` はNo Connectである。`PWR_FLAG` はUSB電源入力とGNDの両方に置く。

回路図を確定する前の必須確認:

- Espressif公式footprint、module pad、thermal via、antenna keepout
- 最終採用buckの参照回路と部品定数
- USB-C receptacleとESD品の実ピン配置
- Qwiic JST SHコネクタのPin 1向き
- H4XのJLCPCBA実装可否とCPL回転

追加の確認事項:

- USB-C、PTC、ESD、MLCCのJLC在庫、PCBA区分、実装フットプリントを発注時に照合する。
- USB shieldをR3のDNP 0 ΩオプションでGNDへ接続するか、RC/chassis接続へ変更するかを筐体方針と併せて決定する。
- `+5V_USB` と `+5V` の保護・レギュレータ接続は、次の電源ブロック追加時に再ERCする。
