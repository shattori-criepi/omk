# BOMレビュー — 電源とUSB入力の対象範囲、2026-08-03 JST

| Ref | Value | MPN | LCSC | Footprint | Status | JLCPCBA |
| --- | --- | --- | --- | --- | --- | --- |
| U2 | AP63203WU-7 | Diodes AP63203WU-7 | C780769 | TSOT-23-6 | Candidate, electrical fit | Verify live assembly listing |
| L1 | 4.7 µH | Coilcraft XGL4020-472MEC | C6012418; live order status VERIFY | `Inductor_SMD:L_Coilcraft_XxL4020` integrated; historical IHLP-2020 rejected | Electrical: ACCEPT; 2D Footprint: ACCEPT / integrated; 3D/Procurement: VERIFY; Release: BLOCKED | JLC lists Extended; PCBA/order-time status VERIFY |
| C1 | 10 µF | Murata GRM21BR61A106KE19L | TBD | 0805 | Nominal: ACCEPT; DC bias: VERIFY; Release: BLOCKED | Verify |
| C2/C3 | 100 nF | Murata GRM155R71C104KA88D | TBD | 0402 | ACCEPT value | Verify live listing |
| C4/C5 | 22 µF | Murata GRM21BR60J226ME39L | C77071 | 0805 | Nominal: ACCEPT; DC bias: VERIFY; Release: BLOCKED | Verify live listing |

出典、計算、layout要件は[power-review.md](power-review.md)を参照。
直接land監査は[xgl4020-footprint-audit.md](xgl4020-footprint-audit.md)に記録しており、footprintまたはBOM/CPL fileは変更していない。

## USB入力／保護の対象範囲

See [usb-power-review.md](usb-power-review.md) and [usb-c-j1-footprint-audit.md](usb-c-j1-footprint-audit.md). J1 DX07S016JA1R1500 (LCSC candidate C3197885), F1 MF-MSMF110-2, D1 USBLC6-2SC6/C7519 and R1/R2 5.1 kΩ are electrical ACCEPT. J1's named KiCad USB2 footprint is retained for review, but official `SJ121837` land/board-edge evidence, 3D/mechanical evidence and JLC live assembly eligibility remain manufacturing-release checks. R3 is 0 Ω DNP tuning option; this review did not modify BOM/CPL data.

## 相互接続の対象範囲

See [../interconnect-review.md](../interconnect-review.md). The bare 2.54 mm concept is rejected for Rev.A. **Main J3 and carrier J2 are integrated** as the selected side-entry TH JST `S4B-PH-K-S(LF)(SN)` / `C157926`, using audited standard KiCad footprint `Connector_JST:JST_PH_S4B-PH-K_1x04_P2.00mm_Horizontal`. Both retain `1=3V3`, `2=GND`, `3=SDA`, `4=SCL`; generators and formal schematics were regenerated, ERC/PDF/SVG were checked. The planned 40 mm nominal harness has PHR-4 housings ×2, SPH-002T-P0.5S contacts ×8, and four AWG26–28 stranded wires, with female housings at both ends. Electrical/2D Footprint: ACCEPT; 3D, PCB rotation/coordinates, cable path, wave fixture and procurement: VERIFY; manufacturing release: BLOCKED.
