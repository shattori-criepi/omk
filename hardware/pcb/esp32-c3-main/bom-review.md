# BOM review — power and USB input scope, 2026-08-03 JST

| Ref | Value | MPN | LCSC | Footprint | Status | JLCPCBA |
| --- | --- | --- | --- | --- | --- | --- |
| U2 | AP63203WU-7 | Diodes AP63203WU-7 | C780769 | TSOT-23-6 | Candidate, electrical fit | Verify live assembly listing |
| L1 | 4.7 µH | Coilcraft XGL4020-472MEC | JLC/LCSC VERIFY | current IHLP-2020 wrong; XGL4020 local FP required | Electrical: ACCEPT; Procurement: VERIFY; Footprint: CHANGE; Release: BLOCKED | unverified |
| C1 | 10 µF | Murata GRM21BR61A106KE19L | TBD | 0805 | Nominal: ACCEPT; DC bias: VERIFY; Release: BLOCKED | Verify |
| C2/C3 | 100 nF | Murata GRM155R71C104KA88D | TBD | 0402 | ACCEPT value | Verify live listing |
| C4/C5 | 22 µF | Murata GRM21BR60J226ME39L | C77071 | 0805 | Nominal: ACCEPT; DC bias: VERIFY; Release: BLOCKED | Verify live listing |

See [power-review.md](power-review.md) for sources, calculations and layout requirements.

## USB input/protection scope

See [usb-power-review.md](usb-power-review.md). J1 DX07S016JA1R1500/C3197885, F1 MF-MSMF110-2, D1 USBLC6-2SC6/C7519 and R1/R2 5.1 kΩ are electrical ACCEPT; JLC live assembly status and footprint/placement audits remain manufacturing-release checks. R3 is 0 Ω DNP tuning option.
