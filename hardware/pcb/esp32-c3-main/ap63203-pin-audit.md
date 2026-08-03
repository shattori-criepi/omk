# AP63203WU-7 pin-by-pin audit

Audit date: 2026-08-03 JST. Scope is only the U2 buck-converter circuit review. This document does **not** change the generator, formal schematic, PCB, footprint files, BOM/CPL, or manufacturing data.

## Evidence and controlled sources

| Source | Revision / date | Used pages |
| --- | --- | --- |
| [Diodes Incorporated, *AP63200/AP63201/AP63203/AP63205* datasheet](https://www.diodes.com/datasheet/download/AP63200-AP63201-AP63203-AP63205.pdf) | DS41326 Rev. 3-2, November 2024; retrieved 2026-08-03 JST | pp. 2–5: pins, limits and electrical characteristics; p. 9: fixed-output circuit; pp. 10–14: EN, protection, component selection and layout; pp. 16–17: ordering/package. |
| [Diodes Incorporated, *AP63203WU-EVM User Guide*](https://www.diodes.com/assets/Evaluation-Boards/AP63203WU-EVM-User-Guide.pdf) | Rev. 2, July 2023; retrieved 2026-08-03 JST | pp. 4–6: 3.3 V configuration, schematic and BOM. |

## Current-design extraction

The generator and generated formal schematic agree on the following U2 circuit.  This audit read them but did not modify either file.

| Item | Current formal design |
| --- | --- |
| U2 | `AP63203WU-7`, manufacturer property `Diodes Inc. AP63203WU-7`, LCSC `C780769`, symbol `Regulator_Switching:AP63203WU`, footprint `Package_TO_SOT_SMD:TSOT-23-6`. |
| Input / enable | U2 pin 3 `VIN` = `+5V`; pin 2 `EN` = `+5V`. C1 10 uF (`GRM21BR61A106KE19L`) and C2 100 nF (`GRM155R71E104KE14D`) are `+5V` to GND. |
| Power path | U2 pin 5 `SW` → L1 pin 1; L1 pin 2 → `+3V3`. L1 is Coilcraft `XGL4020-472MEC`, 4.7 uH, `Inductor_SMD:L_Coilcraft_XxL4020`. |
| Bootstrap | C3 100 nF (`GRM155R71C104KA88D`) is pin 6 `BST` to pin 5 `SW`. |
| Output / feedback | U2 pin 1 `FB` = `+3V3`; C4/C5 are 22 uF each (`GRM21BR60J226ME39L`) from `+3V3` to GND. No external feedback divider is fitted. |
| Ground | U2 pin 4 = GND. TSOT26 is a six-lead package; the datasheet package drawing and assigned six-pad footprint have no exposed pad. |

## Part and pin audit

The exact orderable part is `AP63203WU-7`: AP63203 fixed 3.3 V, 1.1 MHz PWM/PFM, TSOT26, 3,000-piece 7-inch tape-and-reel. It is not the adjustable AP63200/AP63201 and does not use their external divider. Datasheet electrical limits specify AP63203 VFB/output regulation at 3.27–3.33 V in CCM.

| Pin | Datasheet function / requirement | Current net and connection | Status | Audit finding |
| --- | --- | --- | --- | --- |
| 1 FB | Fixed AP63203: connect directly to regulated output. Divider formula and compensation capacitor apply to adjustable AP63200/AP63201, not AP63203. | `+3V3`, after L1 and at C4/C5. | ACCEPT | Matches Figure 21 and the WU-EVM 3.3 V configuration. No feedback resistors are required; calculated output is the fixed 3.3 V, not a divider-derived value. |
| 2 EN | Digital enable; high enables, low disables. Datasheet explicitly permits direct VIN connection or open for automatic start. EN abs. max 35 V. | `+5V` / VIN direct. | ACCEPT | 5 V is within the 3.8–32 V operating range and far below EN abs. max. No programmable UVLO/sequencing is required by current requirements. Startup/inrush remains prototype verification. |
| 3 VIN | 3.8–32 V operating; bypass locally with low-ESR capacitor. Absolute max 35 V DC / 40 V for 400 ms. | `+5V`, C1/C2 to GND. | VERIFY | Electrical voltage range is satisfied. Effective C1 capacitance, capacitor placement, input loop area and USB/PTC transient behavior are later gates. |
| 4 GND | Power ground; datasheet calls for short capacitor returns, GND area beneath device and sufficient GND vias. | GND. | VERIFY | Net is correct. Plane, vias and hot-loop geometry are PCB Phase 3B work. |
| 5 SW | Switching node to LC output; connect L and BST capacitor. VSW abs. max −1.0 V to VIN+0.3 V DC. | `SW` to L1 pin 1 and C3 pin 2. | ACCEPT for schematic / VERIFY for layout | Pin/net mapping matches the required LC and bootstrap topology. Keep copper area small and no unrelated loads/traces on SW in PCB layout. |
| 6 BST | High-side gate-drive bootstrap input; ceramic 100 nF from BST to SW is recommended. VBST limit is VSW−0.3 V to VSW+6.0 V. | C3 pin 1 `BST`; C3 pin 2 `SW`. | ACCEPT | Value and endpoints match datasheet. C3 voltage rating must remain adequate for the bootstrap differential voltage; the specified 16 V X7R part has nominal voltage margin. Physical placement remains Phase 3B VERIFY. |
| Exposed pad | None shown for TSOT26 package. | None in symbol/footprint. | ACCEPT | No missing exposed-pad connection exists. Footprint pad-land dimensional audit is outside this circuit-only audit. |

## External-component audit

| Item | Official recommendation / EVM | Current design | Status | Finding |
| --- | --- | --- | --- | --- |
| C1 input bulk | Datasheet Table 2: 10 uF for AP63203; application text says ceramic **greater than 10 uF** is sufficient for most applications. WU-EVM: 10 uF X5R/X7R, 35 V. | 10 uF, 10 V, X5R, 0805. | VERIFY | Nominal topology matches Table 2. The Murata audit records about 5.28 uF representative effective capacitance at 5 V; worst-condition margin remains unproven, so do not claim adequacy from nominal capacitance. |
| C2 input bypass | WU-EVM adds 100 nF X5R/X7R in parallel with bulk input capacitor. | 100 nF, 25 V, X7R, 0402. | ACCEPT electrically / VERIFY layout | Matches the EVM bypass role and has nominal voltage margin. It must sit in the VIN/GND hot loop with C1. |
| C3 BST–SW | Datasheet and WU-EVM: 100 nF ceramic BST-to-SW. | 100 nF, 16 V, X7R, 0402. | ACCEPT electrically / VERIFY layout | Exact capacitance and required endpoints match. Place immediately across pins 6/5; it is not a capacitor to GND. |
| L1 | Datasheet Table 2 lists 3.9 uH for AP63203; general guidance permits about 2.2–10 uH, DCR <100 mOhm and DC current at least 35% above maximum load. WU-EVM at 12 V uses 6.8 uH / 5 A. | 4.7 uH XGL4020-472MEC; DCR 43.0 mOhm typ / 47.3 mOhm max; Isat 3.0 A; Irms 5.6 A. | ACCEPT electrically; VERIFY 3D/procurement | 4.7 uH lies within official general range. At 5 V → 3.3 V and 1.1 MHz, ideal ripple is about 0.217 A p-p; at 0.85 A load peak is about 0.959 A, and at 2 A load about 2.109 A. This is below the documented 3.0 A Isat and 5.6 A Irms ratings. The different Table-2/EVM values reflect their selected conditions, not a schematic contradiction. |
| C4/C5 output | Datasheet Table 2 and WU-EVM use 2 × 22 uF ceramics (44 uF nominal); general text calls 22–68 uF ceramic sufficient for most applications. | 2 × 22 uF, 6.3 V X5R, 0805. | VERIFY | Nominal 44 uF exactly matches Table 2/EVM topology. The Murata audit records about 11.7 uF each / 23.5 uF total representative effective capacitance at 3.3 V; worst-condition tolerance/temperature margin and stability/transient compliance cannot be released. |
| Feedback divider / Cff | Not used for fixed-output AP63203. EVM includes a 100 pF item in its BOM, but its fixed-output instruction is direct FB-to-output and the current datasheet Figure 21 has no divider/Cff. | None. | ACCEPT | No divider is missing. The EVM C4 is not a basis to add a component without an official schematic/net confirmation. |

## Operating limits and protection relevance

| Parameter | Official value | Design relevance / status |
| --- | --- | --- |
| Recommended VIN / ambient | 3.8–32 V / −40 to +85 °C | `+5V` is within range: ACCEPT. Thermal performance in the enclosure: VERIFY. |
| Minimum on time / frequency | 80 ns typ; 1.1 MHz typ for AP63203 | At 5 V → 3.3 V, ideal on-time is about 600 ns, well above 80 ns: ACCEPT. |
| Current limit | HS peak 2.5/2.8/3.1 A min/typ/max; LS valley 2.5/3.2/3.9 A min/typ/max | Protection is not a guaranteed 2 A continuous thermal design point. L1 supports the stated current levels electrically; thermal/layout: VERIFY. |
| OCP / thermal | Peak limit continuously for 2 ms enters hiccup; 16 ms off then restart. Thermal shutdown typ. 160 °C; restart about 130 °C. | Protection behavior is understood; no substitute for output-short/startup and thermal testing: VERIFY. |
| Soft start / UVLO | 4 ms typ soft start; VIN UVLO rising 3.30–3.70 V, 440 mV typ hysteresis | Adequate for a 5 V source in principle; USB/PTC startup behavior: VERIFY. |
| Thermal reference | TSOT26 theta-JA 89 °C/W under specified single-layer 2 oz/minimum-layout test condition | Do not apply this number directly to final board. Datasheet asks for heat-spreading GND / vias: Phase 3B VERIFY. |

## Datasheet / EVM comparison

| Topic | Current OMK design | Datasheet / WU-EVM | Assessment |
| --- | --- | --- | --- |
| U2, pin map, fixed output | AP63203WU-7; FB direct to 3V3 | Same device family, fixed 3.3 V, FB direct | ACCEPT |
| VIN and EN | 5 V; EN tied to VIN | EVM evaluates at 12 V and offers an EN jumper; datasheet permits EN-to-VIN | ACCEPT; different input/use context is intentional. |
| Input capacitors | 10 uF + 100 nF | 10 uF + 100 nF EVM | ACCEPT nominal; MLCC DC bias/placement VERIFY. |
| Bootstrap | 100 nF BST–SW | 100 nF BST–SW | ACCEPT |
| Inductor | 4.7 uH | Datasheet Table 2 3.9 uH; WU-EVM 6.8 uH at 12 V | ACCEPT electrically; deliberate 5 V application selection within 2.2–10 uH guidance. |
| Output capacitors | 2 × 22 uF | 2 × 22 uF | ACCEPT nominal; MLCC effective capacity VERIFY. |
| EVM extras | No input/output connectors, EN jumper/divider, optional/uncertain 100 pF EVM item, or EVM test points | Evaluation convenience / 12 V test fixture | No circuit CHANGE implied. |
| Layout | Not yet PCB-laid out | Close VIN caps, compact SW/BST loop, GND vias/plane, FB away from SW | BLOCKED for manufacturing release until Phase 3B implementation/review. |

## Disposition and next gate

### ACCEPT

- AP63203WU-7 fixed-3.3 V / TSOT26 part selection, pin 1–6 map and six-pad package intent.
- `VIN=+5V`, `EN=+5V`, `SW→L1 pin 1`, `L1 pin 2→+3V3`, `FB=+3V3`, `GND=GND`, and `BST–SW=C3` net topology.
- No exposed pad and no external feedback divider.
- C2/C3 nominal values; C1 and C4/C5 nominal topology; L1 electrical choice.

### VERIFY

- C1 5 V effective capacitance; C4/C5 3.3 V effective capacitance and transient/stability result.
- U2/C1/C2/C3/L1/C4/C5 physical placement, GND returns/vias, SW copper extent, thermal behavior and antenna/USB separation.
- L1 3D/start-lead orientation and procurement, as already recorded in its separate audit.
- USB/PTC startup, short-circuit and thermal testing.
- Exact TSOT-23-6 land-pattern dimensional audit, if not covered by the Phase 3B footprint/assembly review.

### CHANGE

None identified by this pin-by-pin circuit audit.

### BLOCKED

- Manufacturing release remains blocked until MLCC worst-condition effective-capacitance margin, PCB layout implementation/review, procurement/PCBA checks and prototype electrical/thermal tests close the VERIFY items.

## Phase gate

No schematic change is required by this audit. Phase 3B may proceed with PCB placement/routing of the approved electrical topology, subject to the layout rules in datasheet p. 15: VIN capacitors closest to U2; compact BST–SW loop; L1 then output capacitors; FB at the regulated output and away from SW; sufficient capacitor-ground vias and a large GND heat-spreading layer beneath U2. Manufacturing release remains **BLOCKED**.
