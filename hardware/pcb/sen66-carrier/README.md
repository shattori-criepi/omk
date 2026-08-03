# OMK SEN66 CARRIER Rev.A — Phase 2 schematic input

このディレクトリはSEN66キャリア基板のKiCadプロジェクト配置である。KiCad環境が利用可能になり次第、ここへ `.kicad_pro`、`.kicad_sch`、機構用footprint、ERCレポートを追加する。

現時点の回路図転記元は [Phase 2ネット表](../../../docs/hardware/esp32-c3-sen66-pcb.md#phase-2-回路図設計kicad転記仕様) である。

設計上の不変条件:

- SEN66 6線は全て接続し、Pin 1/6=VDD、2/5=GND、3=SDA、4=SCL。
- I2C pull-upはメイン基板のみ。
- ケーブル穴にはpin番号・信号・Pin 1・引出方向をシルク表示する。
- SEN66の吸気/排気を妨げず、交換可能なretainerおよびストレインリリーフを機構設計へ反映する。
