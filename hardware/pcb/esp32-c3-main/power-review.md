# Phase 3A power review — 2026-08-03 JST

Scope: AP63203WU-7, L1, C1–C5 only. No schematic change is authorized by this review.

## Primary sources

- Diodes Incorporated, *AP63200/AP63201/AP63203/AP63205: 2 A synchronous buck*, retrieved 2026-08-03: https://www.diodes.com/part/view/AP63203
- Diodes Incorporated, *AP63203WU-EVM User Guide*, retrieved 2026-08-03: https://www.diodes.com/assets/Evaluation-Boards/AP63203WU-EVM-User-Guide.pdf
- LCSC AP63203WU-7, C780769, retrieved 2026-08-03: https://fat.lcsc.com/product-detail/C780769.html
- Murata GRM21BR60J226ME39 reference sheet, 2025-01-09: https://search.murata.co.jp/Ceramy/image/img/A01X/G101/ENG/GRM21BR60J226ME39-01A.pdf

## U2 conclusion

AP63203WU-7 is the fixed 3.3 V member of the 2 A synchronous-buck family, with 3.8–32 V input, 1.1 MHz nominal switching, and TSOT-23-6 package. Pins are FB=1, EN=2, IN=3, GND=4, SW=5, BST=6. FB is connected directly at the regulated output for this fixed-output version; no external divider is used. EN must not float: tie to IN for always-on operation. The EVM/datasheet reference circuit uses a ceramic input capacitor at IN/GND, 100 nF BST-to-SW capacitor, 4.7 µH-class inductor and ceramic output capacitance.

| Ref | Current | Decision | Reason |
| --- | --- | --- | --- |
| C1 | 10 µF, 10 V X5R | VERIFY | Nominal value is suitable as bulk input; verify DC-bias effective capacitance at 5 V and place directly at IN/GND. |
| C2 | 100 nF, 25 V X7R | ACCEPT | Local high-frequency input bypass; it supplements rather than replaces C1. |
| C3 | 100 nF X7R | ACCEPT | Matches the BST-to-SW bootstrap capacitor value. Must be directly between BST and SW, not GND. |
| L1 | 4.7 µH, TBD | CHANGE | Value is within the family’s recommended 2.2–10 µH range, but the actual part must be selected before layout. |
| C4/C5 | 22 µF, 6.3 V X5R each | VERIFY | 44 µF nominal ceramic output is not inherently excessive, but the effective value at 3.3 V, tolerance, and startup/load stability must be checked against the latest datasheet/EVM. |

Murata GRM21BR60J226ME39L is 0805, 22 µF ±20%, X5R, 6.3 V. Its reference sheet does not state an application-specific 3.3 V effective capacitance in the static document; obtain SimSurfing curve/approval data before release. Treat C1’s 10 µF X5R effective value likewise as VERIFY. Do not infer an effective value from nominal capacitance.

## L1 recommendation — BLOCKER until JLC live availability is confirmed

| Rank | Manufacturer / MPN | LCSC | L / tol. | Isat / Irms / DCR | Size | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | TAI-TECH UHP252010NF-4R7M | C357306 | 4.7 µH / ±20% | manufacturer datasheet verification required | 2.5×2.0 mm | Candidate; shielded series, confirm exact 4R7 row, Isat ≥2.5 A, Irms and DCR before BOM release. |
| 2 | Murata LQM2MPN4R7MG0L | TBD | 4.7 µH / ±20% | VERIFY | 2.5×2.0 mm | Candidate; request official current rating / LCSC mapping. |
| 3 | Coilcraft XAL4020-472 | TBD | 4.7 µH / ±20% | VERIFY | 4×4 mm | Candidate; excellent thermal margin but cost/PCBA availability must be confirmed. |

No candidate is marked final because live JLC assembly eligibility, the exact manufacturer Isat criterion, temperature-rise current and DCR have not been verified from the individual official data sheet. This remains a BLOCKER.

## 5 V to 3.3 V / 0.85 A estimate

Output power = 2.805 W. Assuming 88–93% efficiency: input current = 0.60–0.64 A. With 4.7 µH and 1.1 MHz, ideal ripple is approximately `(5-3.3)/4.7µH/1.1MHz = 0.33 A p-p`; average inductor current is 0.85 A and peak about 1.02 A before tolerance/transients. Use Isat ≥2.5 A and an Irms rating comfortably above 1 A. U2 loss is approximately 0.21–0.38 W at the stated efficiency range. L1 copper loss is `I²×DCR`; calculate after final DCR selection.

## Phase 3B layout checklist

- Place C1/C2 at U2 IN/GND with the smallest possible hot-loop area.
- Place C3 directly BST–SW; keep SW copper small and away from FB.
- Place L1 immediately at SW, then C4/C5 at the output return.
- Sense FB after L1 at the output capacitor; keep it away from SW/inductor.
- Use a continuous GND plane and several short thermal/return vias at U2 GND/capacitor grounds.
- Keep the buck/SW region away from USB D+/D− and the ESP32 antenna keepout; the Espressif guideline requires a clean antenna area (minimum 15 mm around antenna if it cannot overhang the board).

## Remaining blockers

1. Exact L1 MPN, Isat definition, Irms, DCR, height and live LCSC/JLC PCBA eligibility.
2. C1/C4/C5 effective capacitance from official DC-bias data.
3. Latest AP63203WU-7 EVM schematic must be checked pin-for-pin against the generated schematic during Phase 3B review.
