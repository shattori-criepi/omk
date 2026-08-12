# Phase 3A リリースレビューと回路図変更計画 — 履歴記録

> **プロジェクト状態: CANCELLED (2026-08-04)。製造リリース: CANCELLED。製造許可: NO。**
>
> 本文は中止前の部品・回路図統合レビューの履歴である。本文中の`BLOCKED`、Phase 3B、発注前またはprototype gateは当時の未完了事項を示すだけで、現在の製造承認や次作業を意味しない。後継構成へ自動適用してはならない。詳細は[中止決定記録](../../docs/decisions/esp32-c3-integrated-pcb-cancellation.md)を参照。

当時の対象範囲: Phase 3B PCB layout前の最終棚卸し。本書は履歴としてのレビューおよび変更計画のみを記録する。

## Phase 3Aで確定した決定

| 領域 | 確定した決定 |
| --- | --- |
| MCU | ESP32-C3-MINI-1-H4X; USB D−/D+=GPIO18/GPIO19; SDA/SCL=GPIO6/GPIO7; BOOT=GPIO9; UART RX/TX=GPIO20/GPIO21; GPIO3 status LED remains DNP. |
| Buck | U2 AP63203WU-7, fixed 5 V to 3.3 V / 2 A. C2/C3=100 nF ACCEPT; C1=10 uF and C4/C5=22 uF×2 nominally ACCEPT, DC-bias VERIFY. |
| L1 | Coilcraft XGL4020-472MEC / C6012418, 4.7 uH, is Electrical ACCEPT. `Inductor_SMD:L_Coilcraft_XxL4020` is 2D-footprint ACCEPT and integrated; 3D/mechanical and procurement remain open. |
| USB | J1 JAE DX07S016JA1R1500; D1 ST USBLC6-2SC6; F1 Bourns MF-MSMF110-2; independent 5.1 kOhm Rd; R3 0 Ohm DNP. USB 2.0 Full Speed sink only; no PD. |
| Interconnect | Bare 2.54 mm 1×4 rejected. Selected header: JST S4B-PH-K-S(LF)(SN) / C157926, side-entry TH, for both main J3 and carrier J2. Retain 1=3V3, 2=GND, 3=SDA, 4=SCL. Harness: PHR-4×2, SPH-002T-P0.5S×8, AWG26–28 stranded wire×4, 40 mm nominal, female housings at both ends. |

MPN/form factorおよび電気的mappingは確定している。PCB座標と回転はPhase 3Bの責務であり、footprintを回転してもpad番号や回路図のnet mappingは変わらない。`jst-ph-header-selection.md`を参照。

## 正式回路図の差分監査

### main board

| 項目 | 現在の正式回路図 / generator | 必要な回路図変更一式 | 分類 |
| --- | --- | --- | --- |
| J3 | Generic `Conn_01x04` symbol retained; Value `JST PH 4P`; MPN `S4B-PH-K-S(LF)(SN)`; LCSC `C157926`; `Connector_JST:JST_PH_S4B-PH-K_1x04_P2.00mm_Horizontal`; mapping 1=3V3, 2=GND, 3=SDA, 4=SCL. | Integrated in generator and formal schematic; ERC/PDF/SVG checked. Only PCB placement, 3D and mechanical review remain in Phase 3B. | Integrated / no schematic change remaining |
| L1 | 4.7 uH; MPN `XGL4020-472MEC`; LCSC `C6012418`; `Inductor_SMD:L_Coilcraft_XxL4020`; pad 1=`SW`, pad 2=`+3V3`. | Integrated through generator and formal schematic; ERC/PDF/SVG and unchanged-netlist audit passed. 3D/mechanical and procurement remain Phase 3B/purchase gates. | Integrated / no schematic change remaining |
| J1 | JAE DX07S016JA1R1500 and matching named KiCad footprint; LCSC property `TBD`. USB2 symbol/pad mapping is confirmed; JAE drawing `SJ121837` / spec `JACS-30413` / handling `JAHL-30353-1` contents were not anonymously available. | Obtain JAE controlled geometry, then compare land, shell/NPTH, board-edge, assembly and 3D evidence; only then decide whether to set LCSC C3197885 property. | Required property; footprint-audit gate |
| F1 | MPN Bourns MF-MSMF110-2; 1812 footprint. | Retain electrical part; normalize manufacturer, procurement and release-status properties. | Property only |
| D1 | ST USBLC6-2SC6, LCSC C7519, SOT-23-6 footprint. | Retain MPN/LCSC; normalize manufacturer, procurement and release-status properties. | Property only |
| R1/R2 | 5.1 kOhm, Yageo RC0402FR-075K1L, separate CC1/CC2. | Retain circuit/MPN; add procurement/status after listing confirmation. | Property only |
| R3 | 0 Ohm DNP, shield-to-GND option. | Retain circuit and explicit DNP/assembly/status properties. | Property only |
| C1/C4/C5 | MPNs match review documents. | Retain values/MPNs; add DC-bias VERIFY and release-blocked property/note. | Property only |
| U2 | EN pin 2 and IN pin 3 on +5V; FB pin 1 on +3V3; C3 BST pin 6 to SW pin 5. | No circuit change; record current EVM pin-for-pin check. | Evidence gate |

### SEN66 carrier

| 項目 | 現在の正式回路図 / generator | 必要な回路図変更一式 | 分類 |
| --- | --- | --- | --- |
| J2 | Generic `Conn_01x04` symbol retained; Value `JST PH 4P`; MPN `S4B-PH-K-S(LF)(SN)`; LCSC `C157926`; `Connector_JST:JST_PH_S4B-PH-K_1x04_P2.00mm_Horizontal`; mapping 1=3V3, 2=GND, 3=SDA, 4=SCL. | Integrated with main J3 in generator and formal schematic; ERC/PDF/SVG checked. Only PCB placement, 3D and mechanical review remain in Phase 3B. | Integrated / no schematic change remaining |
| J1 | Six independent TH pads: 1/6=3V3, 2/5=GND, 3=SDA, 4=SCL. | No change; this is the direct-solder SEN66 cable input, not board link. | No change |
| C1/C2 | 100 nF GRM155R71E104KE14D / 10 uF GRM21BR61A106KE19L. | No circuit change. | No change |
| R1/R2 | 4.7 kOhm DNP, LCSC C25900. | No circuit change. | No change |
| TP1–TP4 | 3V3, GND, SDA, SCL respectively. | No change. | No change |

## 承認済み回路図変更一式と順序

1. Task Aの監査は`jst-ph-footprint-audit.md`に記録している。pads 1〜4、pitch、0.75 mm drill geometry、Pad-1識別、2D body mappingはpassした。標準KiCad footprintはACCEPTであり、欠けているSTEP/drawing-viewとfinished-holeの根拠は後続gateとする。
2. **Task B complete:** 両generatorと正式回路図を、S4B-PH-K-S(LF)(SN)、C157926、承認済みKiCad footprintで同時更新した。pin mappingは維持し、mainのERCは0 errors / 既存U1-library warnings 2件、carrierのERCは0 errors / 0 warnings。PCB座標／回転はPhase 3Bの作業として残る。
3. **L1 integration complete:** generatorと正式回路図はXGL4020-472MEC / C6012418 / `Inductor_SMD:L_Coilcraft_XxL4020`を使用する。pad mappingと完全なnetlistを維持し、ERC/PDF/SVGをpassした。履歴上のIHLP-2020 footprintを再利用してはならない。3D/start-leadの向きと調達は後続gateとする。`esp32-c3-main/xgl4020-footprint-audit.md`を参照。
4. J1の監査は`esp32-c3-main/usb-c-j1-footprint-audit.md`に記録している。symbol/pad mappingは受理したが、公開取得できないJAE `SJ121837` / `JACS-30413` / `JAHL-30353-1`により、公式land/edge/assembly geometryはVERIFYのままである。これらを取得して比較を完了してから、LCSC propertyをC3197885へ設定する。
5. netを変更せず、F1、D1、R1/R2、R3、C1/C4/C5のmanufacturer、MPN、LCSC、DNP、assembly、status、release-note propertyを正規化する。
6. AP63203 EVMのpin-for-pin根拠とMurataのDC-biasデータを記録する。これらが現行回路を否定する場合に限り値を変更する。
7. PCB配置前に、再生成、generated/formal結果の比較、ERC、PDF出力、GUI reviewを実行する。

本計画では側面挿入THのS4B-PH-K-S(LF)(SN)を選定する。回路図更新は後続PCB回転と独立しており、回転と機械的配置はPhase 3Bで決定する。

## 残るリリースblocker

| 項目 | 現在の決定 | 未解決の根拠／対応 | Phase | リリースblocker | 担当／対応 |
| --- | --- | --- | --- | --- | --- |
| L1 procurement | XGL4020-472MEC Electrical ACCEPT; LCSC/JLC `C6012418`, Extended listing found | Live stock, price, MOQ and PCBA eligibility in the order flow. | Purchase | Yes | Procurement check. |
| L1 footprint | XGL4020-472MEC / `Inductor_SMD:L_Coilcraft_XxL4020` integrated | 3D/start-lead orientation, assembler paste/mask and placement remain. | Phase 3B / before release | Yes | Hardware design. |
| C1 DC bias | GRM21BR61A106KE19L nominal ACCEPT | Official 5 V SimSurfing/approved curve and minimum effective capacitance. | Before release | Yes | Component review. |
| C4/C5 DC bias | GRM21BR60J226ME39L nominal ACCEPT | Official 3.3 V curves, parallel effective capacity and AP63203 stability check. | Before release | Yes | Component review. |
| AP63203 | Current topology | Current EVM/datasheet pin-for-pin record. | Before PCB | Yes | Hardware design. |
| J1 | DX07S016JA1R1500 electrical ACCEPT; named KiCad USB2 mapping is coherent. | JAE controlled drawing/land/edge, shell stakes/NPTH, paste/mask, mating/3D/assembly and JLC eligibility. Electrical placement planning is conditional; final edge placement is not frozen. | Phase 3B/purchase | Yes | Footprint/procurement. |
| F1 | MF-MSMF110-2 electrical ACCEPT | High-temp derating, voltage drop/heat, inrush/simultaneous-start test; JLC listing. | Prototype/purchase | Yes | Lab/procurement. |
| D1 | USBLC6-2SC6 electrical ACCEPT | JLC class/live listing, placement/return audit. | Purchase/Phase 3B | Yes | Procurement/layout. |
| ESP32-C3 | H4X selected | H4X stock/PCBA condition, 53-pin/footprint audit, exposed-pad vias and antenna keepout. | Phase 3B/purchase | Yes | Hardware/layout. |
| J3/J2 | S4B-PH-K-S(LF)(SN) integrated in both schematics, side-entry TH | 2D footprint accepted; 3D/drawing-view, finished-hole/annular-ring, JLC order-time evidence, CPL rotation and cable exit/strain relief are later gates. | Phase 3B / purchase | Yes | Hardware/mechanical. |
| Mechanics | Coplanar boards, 40 mm harness | Board outlines, M3, SEN66 retainer/airflow, thermal separation and STEP interference. | Phase 3B | Yes | Mechanical/layout. |

## Phase gate

| Gate | 完了必須事項 |
| --- | --- |
| Phase 3A complete | **Complete:** component selection and change plan are complete; manufacturing blockers are explicit. |
| Schematic consolidation | **Complete:** both boards use the accepted standard footprint; mapping was preserved and both schematics regenerated/ERC checked. |
| Phase 3B layout | Exact placement, connector rotation/opening direction, outlines, cable bend/airflow/antenna clearance, USB impedance/trace geometry, thermal vias, CPL rotation and 3D interference. |
| Immediately before order | JLC stock, Basic/Extended, price, PCBA eligibility, MOQ/alternates, approved footprint audits and DC-bias evidence. |
| Prototype evaluation | PTC startup, shield population, ripple, SEN66 voltage drop/temperature offset, EMI/ESD, USB CDC/upload, Wi-Fi/MQTT and 24-hour run. |

## 参照資料

- `hardware/pcb/esp32-c3-main/power-review.md`
- `hardware/pcb/esp32-c3-main/usb-power-review.md`
- `hardware/pcb/interconnect-review.md`
- `hardware/pcb/jst-ph-header-selection.md`
- `docs/hardware/esp32-c3-sen66-pcb.md`
