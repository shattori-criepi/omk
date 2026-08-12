# Phase 3A 相互接続レビュー — 2026-08-03 JST

対象: main J3からcarrier J2までの30〜50 mm・4線ハーネス。回路図の変更はない。

## 決定

**Rev.Aで選定したヘッダは、J3/J2共通でJST `S4B-PH-K-S(LF)(SN)` / LCSC・JLCPCB `C157926`とする。** 側面挿入・直角・スルーホールの4極PHヘッダである。AWG30〜24にはPHR-4ハウジングとSPH-002T-P0.5Sコンタクトを使用する。両基板には、AWG26〜28のより線、仕上がり長40 mm（30〜50 mmを許容）、キー付きハウジング、Pin 1三角印、`1 3V3 / 2 GND / 3 SDA / 4 SCL`のシルクを用いる。ハーネスは**PHR-4×2、SPH-002T-P0.5S×8、4本の配線**で、両端をメスハウジングとする。正確な出典・footprint・向きの根拠は[jst-ph-header-selection.md](jst-ph-header-selection.md)を参照。

SEN66の最大供給電流は350 mAであり、PHの2 A定格には電気的余裕がある。100 kHz・30〜50 mmでは、GNDを連続させ、既存の4.7 kΩ DNP pull-upを必要な場合だけ実装すればI2Cを使用できる。現行の並びではGNDは3V3に隣接するが、各信号には隣接しない。信号完全性を優先する将来の並びは`GND, SDA, SCL, 3V3`だが、両方の回路図変更を伴うためPhase 3Bの提案に留める。未承認の回路図変更を避けるため、Rev.Aでは現行の順序を維持する。

## 比較

| 方式 | ピッチ / 電流 | ロック / キー | 手作業組立 | 決定 |
| --- | --- | --- | --- | --- |
| 裸の2.54 mm 1×4 | 2.54 mm / 十分 | なし | 最も容易 | Rev.AではREJECT。逆挿入がBLOCKER |
| JST PH `S4B-PH-K-S(LF)(SN)` | 2.0 mm / 2 A | friction lock / キー付き | 中程度、広く実用的 | SELECTED: 側面挿入TH、C157926 |
| JST GH | 1.25 mm / 公式電流値を確認 | lock / キー付き | 細ピッチ圧着で難しい | 小型化を優先する場合の代替。BM04B-GHS-TBT、GHR-04V-S、SSHL-002T-P0.2。JLC掲載を確認 |
| JST SH | 1.0 mm / 1 A AWG28 | friction lock / キー付き | 難しい。Qwiicとの取り違えリスク | 推奨しない。SM04B-SRSS-TB、SHR-04V-S。JST出典: https://www.jst.com/products/crimp-style-connectors-wire-to-board-type/sh-connector |

JST PHおよびGHの公式製品データは、記載したハウジング／コンタクトのシリーズを示している。選定したS4B-PH-K-S(LF)(SN)では、LCSC/JLCの`C157926`は現時点でExtended、wave-solder PCBAにはfixtureが必要と掲載されている。実在庫、価格、発注時の適格性は**VERIFY**のままである。KiCadには正確なPH footprint候補があるが、製造リリース前にJST図面、pad番号、Pin 1、courtyard、3D、挿入方向、CPL rotation、手はんだへのアクセスを監査する必要がある。

## 製造リリース前に必要な変更

Electrical: ACCEPT。MPN: ACCEPT。2D Footprint: ACCEPT。調達: VERIFY。3D/Mechanical: Phase 3BでVERIFY。回路図統合: COMPLETE。現行mappingを維持してmain J3とcarrier J2を同時更新した。mainのERCは0 errors / 既存U1-library warnings 2件、carrierのERCは0 errors / 0 warnings。製造リリースは、より広範なJLC、layout、prototypeのgateが完了するまでBLOCKEDとする。両基板に選定済みのキー付き側面挿入THヘッダを実装し、上記の両端メスハウジングのハーネスを用いる。物理的な配置・回転はPhase 3B gateとして残し、対向する開口、束線の曲げ・引き回し、SEN66の気流、基板間隔、手はんだ、JLCPCBAのfixture互換性を確認する。
