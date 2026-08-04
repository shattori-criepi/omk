# OMK SEN66 CARRIER Rev.A — CANCELLED reference design

> **Status: CANCELLED — NOT FOR FABRICATION**
>
> ESP32-C3＋SEN66一体型PCB計画は2026-08-04に正式中止された。このキャリア回路図と関連資料は履歴・参考資料として保持するが、製造可能または動作検証済みではない。後継構成は未決定であり、M5StickS3を含む既製デバイスは候補であって採用決定ではない。詳細は[中止決定記録](../../../docs/decisions/esp32-c3-integrated-pcb-cancellation.md)を参照。

以下は、SEN66の6線ケーブルをESP32-C3メイン基板の4線J3へ変換するキャリア基板の履歴回路図である。PCBレイアウトと製造データは未完了のまま終了した。

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
- J2: メイン基板J3と同時にJST `S4B-PH-K-S(LF)(SN)` / C157926（side-entry TH）へ統合済み。pin order 1=3V3, 2=GND, 3=SDA, 4=SCL。footprintは`Connector_JST:JST_PH_S4B-PH-K_1x04_P2.00mm_Horizontal`。物理向きはPhase 3Bで確認する。

裸2.54 mm案はPhase 3Aで不採用。JST `S4B-PH-K-S(LF)(SN)` / C157926（side-entry TH）を選定し、メイン基板J3とキャリア基板J2を同時に変更する。

Phase 3AのRev.A選定はJST `S4B-PH-K-S(LF)(SN)` / C157926（side-entry TH、両基板共通）である。ハーネスは両端を雌ハウジングとし、PHR-4×2、コンタクト×8、AWG26–28より線×4、完成長40 mmとする。現行pin順は維持し、pin 1→1からpin 4→4の1:1ハーネス以外は禁止する。J3/J2は同一変更セットで更新・再生成済みであり、キャリアERCは0 errors / 0 warnings、PDF/SVG出力も成功した。Phase 3Bでは、両コネクタ開口、PCB上の回転・座標、ケーブル束の曲げ、SEN66 airflow、基板間距離、手はんだ性、JLCPCBA治具対応を確認する。

メインR6/R7とキャリアR1/R2はともに4.7 kΩ DNPであり、実装時に必要な箇所だけ有効化する。キャリア電源容量はC1=100 nF、C2=10 µFであり、旧47 µF案は現行回路図には採用していない。

## 再生成・検証

```bash
KICAD_SYMBOL_DIR=/usr/share/kicad/symbols .venv/bin/python scripts/generate_schematic.py
kicad-cli sch erc sen66-carrier.kicad_sch --output erc-report.txt
kicad-cli sch export pdf sen66-carrier.kicad_sch --output sen66-carrier-schematic.pdf
kicad-cli sch export svg sen66-carrier.kicad_sch --output rendered/
```

`kicad-sch-api==0.5.6`で空テンプレートを読込み、生成中間物のERCが0 errorsの場合だけ正式回路図へ反映する。表示はReferenceと短いValueのみで、型番・LCSC・Footprint・Statusはシンボルプロパティとして保持し非表示にする。
