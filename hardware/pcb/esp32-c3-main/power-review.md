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
| L1 | 4.7 µH | Electrical: ACCEPT; 2D footprint: ACCEPT / integrated; 3D/mechanical: VERIFY; Procurement: VERIFY; Manufacturing release: BLOCKED | XGL4020-472MEC / C6012418 is integrated through the generator; see final status below. |
| C4/C5 | 22 µF, 6.3 V X5R each | VERIFY | 44 µF nominal ceramic output is not inherently excessive, but the effective value at 3.3 V, tolerance, and startup/load stability must be checked against the latest datasheet/EVM. |

Murata GRM21BR60J226ME39L is 0805, 22 µF ±20%, X5R, 6.3 V. Its reference sheet does not state an application-specific 3.3 V effective capacitance in the static document; obtain SimSurfing curve/approval data before release. Treat C1’s 10 µF X5R effective value likewise as VERIFY. Do not infer an effective value from nominal capacitance.

## Initial candidate comparison (superseded by final status below)

| Rank | Manufacturer / MPN | LCSC | L / tol. | Isat / Irms / DCR | Size | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Historical | TAI-TECH UHP252010NF-4R7M | C357306 | 4.7 µH / ±20% | verify exact row | 2.5×2.0 mm | superseded alternative |
| Historical | Murata LQM2MPN4R7MG0L | TBD | 4.7 µH / ±20% | 1.1 A / 175 mΩ max | 2.5×2.0 mm | rejected |

This historical table is not the current ranking.

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

1. L1 procurement: JLCPCB SMT eligibility, LCSC/JLC part number, live stock and Basic/Extended class.
2. L1 3D/mechanical: the audited `Inductor_SMD:L_Coilcraft_XxL4020` and XGL4020-472MEC / C6012418 are integrated through the generator and formal schematic; ERC/PDF/SVG passed. Confirm 3D model, short/start-lead orientation and Phase 3B placement. The historical `Inductor_SMD:L_Vishay_IHLP-2020` is incompatible. See [xgl4020-footprint-audit.md](xgl4020-footprint-audit.md).
3. C1/C4/C5 effective capacitance from official DC-bias data.
4. Latest AP63203WU-7 EVM schematic must be checked pin-for-pin during Phase 3B review.

## Phase 3A final-selection addendum — 2026-08-03 JST

### L1

**Recommended electrical part: Coilcraft XGL4020-472MEC.** Coilcraft's official product page specifies 4.7 µH ±20%, DCR 43.0 mΩ typ / 47.3 mΩ max, Isat 3.0 A at 20% inductance drop, and Irms 5.6 A for 40°C rise. It is a shielded composite 4.0×4.0 mm part with 2.10 mm maximum height, −40 to +125°C at the 40°C-rise rating. Source and retrieval date: https://www.coilcraft.com/en-us/products/power/high-voltage-inductors/xgl/xgl4020/xgl4020-472/ (2026-08-03 JST).

At 5 V → 3.3 V, 1.1 MHz, L=4.7 µH, the ideal ripple is 0.33 A p-p. Peak current is 1.02 A at 0.85 A load and 2.17 A at 2 A load. With 43 mΩ typ DCR, copper loss is approximately 31 mW at 0.85 A and 174 mW at 2 A; both are below the 5.6 A / 40°C thermal reference rating. Isat margin is 2.9× at 0.85 A peak and 1.38× at 2 A peak. Irms margin is 6.6× and 2.8× respectively. Isat is not interchangeable across makers: Coilcraft defines it by stated inductance drop at 25°C, and Irms by temperature rise.

TAI-TECH UHP252010NF-4R7M / LCSC C357306 remains an **alternative only**: its official data sheet defines Isat as ≤30% inductance change and Irms as 40°C rise, but its exact 4R7 row and live PCBA listing must be checked at order. Murata LQM2MPN4R7MG0L is **rejected** for this design: public manufacturer/distributor data states 1.1 A and 175 mΩ max, below the required continuous-current margin. The historical `Inductor_SMD:L_Vishay_IHLP-2020` footprint does not match the recommended Coilcraft 4×4 mm body. The completed audit accepted, and the generator/formal schematic now use, `Inductor_SMD:L_Coilcraft_XxL4020`; ERC/PDF/SVG passed with no L1 warning. 3D/start-lead orientation and procurement remain release gates.

The official LCSC/JLC pages identify XGL4020-472MEC as `C6012418`, SMD 4×4 mm and Extended; live stock, price, PCBA eligibility and order-time availability remain **VERIFY at purchase**. This is a procurement blocker, not an electrical-selection blocker. The 3D model/start-lead orientation remains a Phase 3B mechanical VERIFY.

### MLCC DC bias

Murata's official reference sheet confirms GRM21BR61A106KE19L is 0805, 10 µF, 10 V, X5R and GRM21BR60J226ME39L is 0805, 22 µF, 6.3 V, X5R. The accessible official sheets do not contain the SimSurfing DC-bias curves needed to state a 5 V or 3.3 V effective capacitance. Therefore no numerical effective-capacitance value is asserted: **C1, C4 and C5 remain VERIFY**. Nominal topology remains acceptable; do not add capacitors or change voltage ratings until the exact Murata SimSurfing curves/approval sheets are archived. This unresolved primary-data requirement remains a BLOCKER for manufacturing release.
