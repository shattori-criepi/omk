# OMK SEN66 CARRIER Rev.A

SEN66の6線ケーブルを、ESP32-C3メイン基板の4線J3へ変換するキャリア基板のKiCad 9.0.8回路図である。PCBレイアウトは未着手である。

## 正式な接続

| SEN66 cable pad J1 | 信号 | Main-side J2 | 信号 |
| --- | --- | --- | --- |
| 1 | +3V3 (VDD) | 1 | +3V3 |
| 2 | GND | 2 | GND |
| 3 | I2C_SDA | 3 | I2C_SDA |
| 4 | I2C_SCL | 4 | I2C_SCL |
| 5 | GND | — | J2 pin 2へ集約 |
| 6 | +3V3 (VDD) | — | J2 pin 1へ集約 |

J1は切断したSEN66 JST-GHケーブルを直接はんだ付けする6個のスルーホール表現である。ケーブル色は規定しない。被覆を剥いた各線は**必ず1線ずつ別のスルーホール**へ入れ、組立前にPin 1から導通確認する。PCB Phaseでストレインリリーフ穴、結束バンド用スロット、線材径、引出方向、SEN66本体との干渉を決める。

## 部品と選定

- C1: 100 nF / Murata GRM155R71E104KE14D / LCSC C1525、PCBA basic候補
- C2: 10 uF / Murata GRM21BR61A106KE19L / LCSC C15850、PCBA区分・在庫は発注時確認
- R1/R2: 4.7 kΩ DNP I²C pull-up / LCSC C25900。通常はメイン基板側のpull-upを使用する。
- J2: メイン基板J3と同一の2.54 mm 1×4、pin order 1=3V3, 2=GND, 3=SDA, 4=SCL。現行案はRA socketで手はんだ。

2.54 mm案にはロックがない。次版の推奨候補はJST-PH 4ピン（B4B-PH-K-S + PHR-4）またはJST-GH 4ピンであり、採用にはメイン基板J3も同時に変更する承認が必要である。

メインR6/R7とキャリアR1/R2はともに4.7 kΩ DNPであり、実装時に必要な箇所だけ有効化する。キャリア電源容量はC1=100 nF、C2=10 µFであり、旧47 µF案は現行回路図には採用していない。

## 再生成・検証

```bash
KICAD_SYMBOL_DIR=/usr/share/kicad/symbols .venv/bin/python scripts/generate_schematic.py
kicad-cli sch erc sen66-carrier.kicad_sch --output erc-report.txt
kicad-cli sch export pdf sen66-carrier.kicad_sch --output sen66-carrier-schematic.pdf
kicad-cli sch export svg sen66-carrier.kicad_sch --output rendered/
```

`kicad-sch-api==0.5.6`で空テンプレートを読込み、生成中間物のERCが0 errorsの場合だけ正式回路図へ反映する。表示はReferenceと短いValueのみで、型番・LCSC・Footprint・Statusはシンボルプロパティとして保持し非表示にする。
