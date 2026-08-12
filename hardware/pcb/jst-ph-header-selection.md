# JST PH 基板ヘッダ選定 — 2026-08-03 JST

対象: main J3およびcarrier J2の基板ヘッダ。この選定では回路図、PCB、generator、footprint、BOM/CPLを変更しない。

## 決定

**main J3とcarrier J2の両方に`S4B-PH-K-S(LF)(SN)`を選定する。** JST PHの4極・2.00 mm pitch・側面挿入／直角・スルーホールで、shroudedかつpolarizedの嵌合interfaceを持つ。JSTでの基礎modelは`S4B-PH-K-S`であり、JST公式PH catalogueでは本製品を`(LF)(SN)`表記としている。LCSC/JLCでは購入可能な完全MPNを`S4B-PH-K-S(LF)(SN)` / `C157926`として掲載している。

選定する嵌合ハーネスは、PHR-4ハウジング×2、SPH-002T-P0.5Sコンタクト×8、AWG26〜28のより線4本、仕上がり長40 mm、両端メスハウジングのままとする。既存の電気的mappingも変更しない。1=3V3、2=GND、3=SDA、4=SCL。

基板は同一平面で横並びに配置するため、vertical THよりside-entry THを優先する。対向する基板端の間をハーネスが横方向に通り、ESP32 antenna側から離し、SEN66の気流範囲で垂直ループを作らずに済む。through-hole postとclinched/kinkedの保持構造は、選定したSMT比較候補よりもRev.Aの手作業組立に対して寛容である。

## 公式出典の記録

| 出典 | 確認済み情報 |
| --- | --- |
| JST, [PH connector catalogue (ePH.pdf)](https://www.jst-mfg.com/product/pdf/eng/ePH.pdf), retrieved 2026-08-03 JST | PH is 2.00 mm; S4B-PH-K-S is the four-circuit side-entry TH header; 2 A AC/DC (AWG24), 100 V AC/DC, −40 to +105 °C, AWG32–24, PCB thickness 0.8–1.6 mm. It identifies `S` as side-entry, shows a No. 1 circuit mark, and states the `(LF)(SN)` label notation. The illustrated TH layout uses 2.00 mm pitch and `φ0.7 +0.1/0` hole guidance; JST notes the hole size depends on board material/process. |
| [JST PH product page](https://www.jst-mfg.com/product/index.php?lang=2&series=199), retrieved 2026-08-03 JST | Lists B4B-PH-K-S, S4B-PH-K-S and PH SMT header families. |
| [LCSC C157926](https://www.lcsc.com/product-detail/Wire-To-Board-Wire-To-Wire-Connector_JST-Sales-America_S4B-PH-K-S-LF-SN_JST-Sales-America-S4B-PH-K-S-LF-SN_C157926.html), checked 2026-08-03 JST | MPN, 4-position right-angle TH form, and live-stock snapshot. Live data is non-binding. |
| [JLCPCB C157926](https://jlcpcb.com/partdetail/JST_SalesAmerica-S4B_PH_K_S_LF_SN/C157926), checked 2026-08-03 JST | Extended part; wave-solder assembly, Economic/Standard PCBA listing; JLC says a PCB-assembly fixture is required. Live listing, price and eligibility must be rechecked at order. |

JLCの掲載では、right-angle bodyを長さ9.9 mm、奥行き7.6 mm、基板上高さ4.8 mmとしている。最終footprint監査ではmarketplaceの表示だけに依存せず、JST図面を管理された出典として使用する。

## 候補比較

| 候補 | 形状 | 結果 | 理由 |
| --- | --- | --- | --- |
| `B4B-PH-K-S(LF)(SN)` / C131334 | Top-entry TH | Not selected | Electrically valid and hand-solder-friendly, but a coplanar 40 mm harness must rise vertically, bend, then descend. This increases height, can obstruct SEN66 airflow and can route near the antenna. |
| **`S4B-PH-K-S(LF)(SN)` / C157926** | **Side-entry TH** | **Selected** | Natural lateral exit, keyed/polarized PH interface, 2 A rating versus 0.35 A SEN66 peak, robust TH retention and direct KiCad 9 footprint. |
| `S4B-PH-SM4-TB` | Side-entry SMT | Alternative only | Official PH catalogue and KiCad library confirm the model/footprint, but it needs SMT-land/paste/CPL control and has lower hand-rework/land-peel margin under cable insertion than the TH part. Consider only when PCBA priority or board area outweighs Rev.A hand-build robustness. |
| `SM04B-PASS-TBT` | Not PH | Rejected | It is a different JST series; do not substitute it into the PH PHR-4/SPH-002T-P0.5S harness system. |

## footprintと組立計画

導入済みKiCad 9 libraryには、正確な候補footprintが含まれている。

```text
Connector_JST:JST_PH_S4B-PH-K_1x04_P2.00mm_Horizontal
```

pads 1〜4は2.00 mm pitchで、through-hole属性、silkscreenのNo. 1表示、courtyard/fab layer、STEP-model referenceを持つ。これは**footprint候補であり、まだACCEPTではない**。回路図更新前に、pad位置／径、outline、pin-1 mark、基板端clearance、solder access、3D bodyを管理されたJST図面と比較する。J3/J2の現行汎用2.54 mm footprintはCHANGEとする。

各headerは基板間の隙間に向く基板端へ配置する。connector body/depthと最小bend envelope以上のcable-exit corridorを確保し、最終clearanceはPhase 3Bの機械的確認とする。J3のcorridorはESP32 antenna keepoutから離し、J2のcorridor、strain relief、harnessはSEN66の吸気／排気領域から外す。基板silkにはPin-1 triangle、`1 3V3`、`2 GND`、`3 SDA`、`4 SCL`、connector outline、cable-exit arrowを追加する。

## pin方向の制御と回路図統合gate

The selected electrical harness is **1:1**: pin 1→pin 1, pin 2→pin 2, pin 3→pin 3 and pin 4→pin 4. A 1↔4 reversal is prohibited. PCB rotation never changes a footprint's pad numbers or the schematic net mapping. Therefore the physical board coordinates and rotations are **not** a prerequisite for schematic consolidation.

The following are Phase 3B placement/mechanical checks: opposing-edge opening direction, any bundle-level bend or gentle turn, bend radius, airflow, antenna clearance, strain relief, silk reading direction and CPL rotation. A bundle may turn as a whole; it does not require changing individual conductor order.

Task A was executed on 2026-08-03. Pad numbering, 2.00 mm pitch, 0.75 mm drill geometry, Pad-1 identification and 2D body mapping pass; the standard KiCad footprint is accepted. **Schematic consolidation is GO.** See [jst-ph-footprint-audit.md](jst-ph-footprint-audit.md). The missing STEP model is an environment-package gap and is a Phase 3B mechanical VERIFY, not a schematic blocker. Manufacturing release remains BLOCKED pending layout, procurement and prototype evidence.

## 次の作業

### Task A — footprint監査

完了。 [jst-ph-footprint-audit.md](jst-ph-footprint-audit.md)を参照。2D footprintは受理済みであり、STEP/drawing-viewとfinished-hole conventionは後続の機械／製造確認とする。

### Task B — 回路図統合

Task B is complete: main J3 and carrier J2 were updated together through their generators, with MPN `S4B-PH-K-S(LF)(SN)`, LCSC `C157926` and `Connector_JST:JST_PH_S4B-PH-K_1x04_P2.00mm_Horizontal`. The fixed mapping was preserved and both formal schematics were regenerated; main ERC is 0 errors / 2 existing U1-library warnings, carrier ERC is 0 errors / 0 warnings, and PDF/SVG outputs were regenerated. The separate L1 footprint issue may proceed in its own change set.

## 状態と残るblocker

| 項目 | Electrical | MPN | 2D Footprint | 3D/Mechanical | 調達 | 回路図統合 | PCB配置／向き | 製造リリース |
| --- | --- | --- | --- | --- | --- | --- | --- |
| main J3 / carrier J2, S4B-PH-K-S(LF)(SN) | ACCEPT | ACCEPT | ACCEPT | VERIFY | VERIFY | COMPLETE | Phase 3B | BLOCKED |

Remaining evidence for release: Phase 3B STEP/drawing-view, body/opening, mating-space, cable bend/airflow/antenna/strain-relief checks; JLC finished-hole/annular-ring, order-time stock, price, Extended classification, fixture/PCBA confirmation and CPL rotation.
