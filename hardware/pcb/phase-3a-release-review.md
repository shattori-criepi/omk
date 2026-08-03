# Phase 3A release review and schematic-change plan — 2026-08-03 JST

Scope: final inventory before Phase 3B PCB layout. This is a review and change plan only: no schematic, PCB, generator, footprint, BOM/CPL, commit or push is changed.

## Decisions fixed by Phase 3A

| Area | Fixed decision |
| --- | --- |
| MCU | ESP32-C3-MINI-1-H4X; USB D−/D+=GPIO18/GPIO19; SDA/SCL=GPIO6/GPIO7; BOOT=GPIO9; UART RX/TX=GPIO20/GPIO21; GPIO3 status LED remains DNP. |
| Buck | U2 AP63203WU-7, fixed 5 V to 3.3 V / 2 A. C2/C3=100 nF ACCEPT; C1=10 uF and C4/C5=22 uF×2 nominally ACCEPT, DC-bias VERIFY. |
| L1 | Coilcraft XGL4020-472MEC, 4.7 uH, is Electrical ACCEPT. Procurement and footprint remain unresolved. |
| USB | J1 JAE DX07S016JA1R1500; D1 ST USBLC6-2SC6; F1 Bourns MF-MSMF110-2; independent 5.1 kOhm Rd; R3 0 Ohm DNP. USB 2.0 Full Speed sink only; no PD. |
| Interconnect | Bare 2.54 mm 1×4 rejected. Selected header: JST S4B-PH-K-S(LF)(SN) / C157926, side-entry TH, for both main J3 and carrier J2. Retain 1=3V3, 2=GND, 3=SDA, 4=SCL. Harness: PHR-4×2, SPH-002T-P0.5S×8, AWG26–28 stranded wire×4, 40 mm nominal, female housings at both ends. |

The MPN/form factor and electrical mapping are fixed. PCB coordinates and rotations are Phase 3B responsibilities; rotating a footprint does not change pad numbering or the schematic net mapping. See `jst-ph-header-selection.md`.

## Formal-schematic delta audit

### Main board

| Item | Formal schematic / generator now | Required schematic-change set | Classification |
| --- | --- | --- | --- |
| J3 | Generic `Conn_01x04`; 2.54 mm horizontal header footprint; MPN `TBD`; mapping 1=3V3, 2=GND, 3=SDA, 4=SCL. | Replace with JST S4B-PH-K-S(LF)(SN) / C157926 and its audited KiCad footprint; retain mapping. | Required connection + property + footprint |
| L1 | 4.7 uH; MPN `TBD`; `Inductor_SMD:L_Vishay_IHLP-2020`; note says TBD. | Set MPN XGL4020-472MEC; replace incompatible footprint with verified XGL4020 footprint. | Required property + footprint |
| J1 | JAE DX07S016JA1R1500 and matching named KiCad footprint; LCSC property `TBD`. | Set LCSC C3197885 after official drawing/footprint audit. | Required property; footprint-audit gate |
| F1 | MPN Bourns MF-MSMF110-2; 1812 footprint. | Retain electrical part; normalize manufacturer, procurement and release-status properties. | Property only |
| D1 | ST USBLC6-2SC6, LCSC C7519, SOT-23-6 footprint. | Retain MPN/LCSC; normalize manufacturer, procurement and release-status properties. | Property only |
| R1/R2 | 5.1 kOhm, Yageo RC0402FR-075K1L, separate CC1/CC2. | Retain circuit/MPN; add procurement/status after listing confirmation. | Property only |
| R3 | 0 Ohm DNP, shield-to-GND option. | Retain circuit and explicit DNP/assembly/status properties. | Property only |
| C1/C4/C5 | MPNs match review documents. | Retain values/MPNs; add DC-bias VERIFY and release-blocked property/note. | Property only |
| U2 | EN pin 2 and IN pin 3 on +5V; FB pin 1 on +3V3; C3 BST pin 6 to SW pin 5. | No circuit change; record current EVM pin-for-pin check. | Evidence gate |

### SEN66 carrier

| Item | Formal schematic / generator now | Required schematic-change set | Classification |
| --- | --- | --- | --- |
| J2 | Generic `Conn_01x04`; 2.54 mm horizontal socket footprint; mapping 1=3V3, 2=GND, 3=SDA, 4=SCL. | Change together with main J3 to S4B-PH-K-S(LF)(SN) / C157926; retain mapping and audit pin 1 in Task A. | Required connection + property + footprint |
| J1 | Six independent TH pads: 1/6=3V3, 2/5=GND, 3=SDA, 4=SCL. | No change; this is the direct-solder SEN66 cable input, not board link. | No change |
| C1/C2 | 100 nF GRM155R71E104KE14D / 10 uF GRM21BR61A106KE19L. | No circuit change. | No change |
| R1/R2 | 4.7 kOhm DNP, LCSC C25900. | No circuit change. | No change |
| TP1–TP4 | 3V3, GND, SDA, SCL respectively. | No change. | No change |

## Approved schematic-change set and order

1. Task A audit is recorded in `jst-ph-footprint-audit.md`: pads 1–4, pitch, 0.75 mm drill geometry, Pad-1 identification and 2D body mapping pass. The standard KiCad footprint is ACCEPT; missing STEP/drawing-view and finished-hole evidence are later gates.
2. **Task B complete:** both generators and formal schematics were updated together with S4B-PH-K-S(LF)(SN), C157926 and the accepted KiCad footprint. The pin mapping was preserved; main ERC is 0 errors / 2 existing U1-library warnings and carrier ERC is 0 errors / 0 warnings. PCB coordinates/rotations remain Phase 3B work.
3. Obtain Coilcraft XGL4020 drawing/land pattern; create/audit local footprint, then update L1 MPN, note and footprint. The present IHLP-2020 footprint must not be reused.
4. Complete J1 drawing-to-footprint audit, then set its LCSC property to C3197885 if it passes.
5. Normalize manufacturer, MPN, LCSC, DNP, assembly, status and release-note properties for F1, D1, R1/R2, R3 and C1/C4/C5 without changing nets.
6. Archive AP63203 EVM pin-for-pin evidence and Murata DC-bias data. Change values only if that evidence disproves the present circuit.
7. Regenerate, compare generated/formal results, run ERC, export PDF and perform GUI review before PCB placement.

This plan selects side-entry TH S4B-PH-K-S(LF)(SN). Its schematic update is independent of later PCB rotation; Phase 3B determines the rotations and mechanical arrangement.

## Remaining release blockers

| Item | Current decision | Unresolved evidence/action | Phase | Release blocker | Owner/action |
| --- | --- | --- | --- | --- | --- |
| L1 procurement | XGL4020-472MEC Electrical ACCEPT | LCSC/JLC number, live stock, Basic/Extended, SMT eligibility. | Purchase | Yes | Procurement check. |
| L1 footprint | XGL4020-472MEC | Coilcraft drawing/land pattern, local footprint, pads 1/2, courtyard/paste/mask/height, DRC/3D. | Before schematic/PCB release | Yes | Hardware design. |
| C1 DC bias | GRM21BR61A106KE19L nominal ACCEPT | Official 5 V SimSurfing/approved curve and minimum effective capacitance. | Before release | Yes | Component review. |
| C4/C5 DC bias | GRM21BR60J226ME39L nominal ACCEPT | Official 3.3 V curves, parallel effective capacity and AP63203 stability check. | Before release | Yes | Component review. |
| AP63203 | Current topology | Current EVM/datasheet pin-for-pin record. | Before PCB | Yes | Hardware design. |
| J1 | DX07S016JA1R1500 electrical ACCEPT | Board edge, shell stakes/NPTH, land, paste/mask/courtyard, 3D and JLC eligibility. | Phase 3B/purchase | Yes | Footprint/procurement. |
| F1 | MF-MSMF110-2 electrical ACCEPT | High-temp derating, voltage drop/heat, inrush/simultaneous-start test; JLC listing. | Prototype/purchase | Yes | Lab/procurement. |
| D1 | USBLC6-2SC6 electrical ACCEPT | JLC class/live listing, placement/return audit. | Purchase/Phase 3B | Yes | Procurement/layout. |
| ESP32-C3 | H4X selected | H4X stock/PCBA condition, 53-pin/footprint audit, exposed-pad vias and antenna keepout. | Phase 3B/purchase | Yes | Hardware/layout. |
| J3/J2 | S4B-PH-K-S(LF)(SN) integrated in both schematics, side-entry TH | 2D footprint accepted; 3D/drawing-view, finished-hole/annular-ring, JLC order-time evidence, CPL rotation and cable exit/strain relief are later gates. | Phase 3B / purchase | Yes | Hardware/mechanical. |
| Mechanics | Coplanar boards, 40 mm harness | Board outlines, M3, SEN66 retainer/airflow, thermal separation and STEP interference. | Phase 3B | Yes | Mechanical/layout. |

## Phase gates

| Gate | Must be complete |
| --- | --- |
| Phase 3A complete | **Complete:** component selection and change plan are complete; manufacturing blockers are explicit. |
| Schematic consolidation | **Complete:** both boards use the accepted standard footprint; mapping was preserved and both schematics regenerated/ERC checked. |
| Phase 3B layout | Exact placement, connector rotation/opening direction, outlines, cable bend/airflow/antenna clearance, USB impedance/trace geometry, thermal vias, CPL rotation and 3D interference. |
| Immediately before order | JLC stock, Basic/Extended, price, PCBA eligibility, MOQ/alternates, approved footprint audits and DC-bias evidence. |
| Prototype evaluation | PTC startup, shield population, ripple, SEN66 voltage drop/temperature offset, EMI/ESD, USB CDC/upload, Wi-Fi/MQTT and 24-hour run. |

## References

- `hardware/pcb/esp32-c3-main/power-review.md`
- `hardware/pcb/esp32-c3-main/usb-power-review.md`
- `hardware/pcb/interconnect-review.md`
- `hardware/pcb/jst-ph-header-selection.md`
- `docs/hardware/esp32-c3-sen66-pcb.md`
