# USBLC6-2SC6 placement and return-path audit

**Audit date:** 2026-08-03 JST

**Scope:** USB-C J1 to ESP32-C3-MINI-1 U1 USB D+/D− protection only. The actual formal reference is **D1** (not U3): `USBLC6-2SC6`. This audit records circuit/topology and Phase 3B placement requirements only; it does not modify the generator, formal schematic, PCB, footprint, BOM/CPL, or Gerber files.

## Sources

| Source | Revision / date | Used for |
| --- | --- | --- |
| [ST USBLC6-2 datasheet, DS4260](https://www.st.com/resource/en/datasheet/usblc6-2.pdf) | Rev 7, December 2021; retrieved 2026-08-03 JST | Pinout, rail-to-rail protection topology, capacitance, layout optimization, USB application, SOT23-6L and recommended land data (pp. 1–2, 5–6, 9, 11–12). |
| [ST AN5686, PCB layout tips to maximize ESD protection efficiency](https://www.st.com/resource/en/application_note/an5686-pcb-layout-tips-to-maximize-esd-protection-efficiency-stmicroelectronics.pdf) | Rev 1; retrieved 2026-08-03 JST | Connector → TVS → IC routing, short TVS path and multiple GND vias (p. 8). |
| [ESP32-C3 Hardware Design Guidelines — PCB Layout](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32c3/pcb-layout-design.html) | latest, retrieved 2026-08-03 JST | USB pair impedance, equal-length parallel routing, reference plane, ground-via rule and antenna separation. |
| [ESP32-C3 Hardware Design Guidelines — Schematic Checklist](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32c3/schematic-checklist.html) | latest, retrieved 2026-08-03 JST | GPIO18 = D− / GPIO19 = D+, and chip-side optional 22/33 Ω series and D+/D− capacitor footprints. |
| Project formal sources | `scripts/generate_schematic.py`, `esp32-c3-main.kicad_sch`, KiCad 9 standard symbol/footprint files; read 2026-08-03 JST | Current D1/J1/U1 values, nets, pin numbers and assigned land pattern. |

## Current design extraction

| Item | Current value | Assessment |
| --- | --- | --- |
| Reference / value | `D1` / `USBLC6-2SC6` | ACCEPT |
| MPN / LCSC property | `STMicroelectronics USBLC6-2SC6` / `C7519` | ACCEPT for recorded identity; procurement/live PCBA status is VERIFY. |
| Package | ST SOT23-6L (`USBLC6-2SC6`; the smaller SOT-666 is the `-2P6` variant) | ACCEPT |
| Symbol | `Power_Protection:USBLC6-2SC6` | ACCEPT — it extends the KiCad USBLC6-2P6 symbol and has pins 1–6. |
| Footprint | `Package_TO_SOT_SMD:SOT-23-6` | ACCEPT for pin-numbered package assignment; its final land/stencil/3D/PCBA validation is VERIFY. |
| VBUS decoupling in topology | D1.5 is `+5V`; C2 is 100 nF from `+5V` to GND, with C1 10 µF in parallel on the same rail. | ACCEPT electrically. In Phase 3B C2 must be physically close to D1.5/GND; schematic proximity is not a layout result. |

DS4260 lists 3.5 pF maximum I/O-to-GND capacitance and explicitly supports USB 2.0 up to 480 Mb/s, so it is electrically suitable for ESP32-C3 USB Full-Speed. This audit does not make a new device-selection decision.

## Pin mapping audit

ST’s functional diagram (top view) defines 1/6 as I/O1, 3/4 as I/O2, 2 as GND and 5 as VBUS. The KiCad SOT-23-6 footprint likewise uses pads 1–3 on one package side and 4–6 on the other; pad 1 is identified by the F.SilkS triangle.

| D1 pin | ST function | Current net | Peer pins / result | Status |
| --- | --- | --- | --- | --- |
| 1 | I/O1 | `USB_D+` | J1 A6/B6; D1.6; U1.27 (GPIO19/USB_D+) | ACCEPT |
| 6 | I/O1 | `USB_D+` | D1.1; same D+ net; no D+/D− swap | ACCEPT |
| 3 | I/O2 | `USB_D-` | J1 A7/B7; D1.4; U1.26 (GPIO18/USB_D−) | ACCEPT |
| 4 | I/O2 | `USB_D-` | D1.3; same D− net; no D+/D− swap | ACCEPT |
| 2 | GND | `GND` | J1 ground / system GND; not `USB_SHIELD` | ACCEPT electrically; return-path geometry VERIFY |
| 5 | VBUS | `+5V` | Post-F1 +5 V, C1/C2 and buck input | ACCEPT electrically; D1.5-to-C2 loop geometry VERIFY |

I/O1 and I/O2 are electrically symmetric inside each pair. Nevertheless, the PCB must orient D1 deliberately: route J1 into pads 1/3 (connector side) and out of pads 6/4 (chip side), or the 180° equivalent that preserves this through-path. This avoids using a T branch as an ESD stub. The current net labels intentionally make each pair one net; they do not authorize a forked layout.

## USB D+/D− signal and ESD return paths

### Intended signal path

```text
J1 A6/B6 (D+) → D1 I/O1 connector-side pad → D1 I/O1 chip-side pad → U1.27 GPIO19 (USB_D+)
J1 A7/B7 (D−) → D1 I/O2 connector-side pad → D1 I/O2 chip-side pad → U1.26 GPIO18 (USB_D−)
```

The connector has duplicated USB2 pins as expected for Type-C. The D+ and D− net identities are preserved end-to-end, and Espressif specifies GPIO19 as D+ and GPIO18 as D−. **No schematic CHANGE is indicated.**

### ESD discharge return path

USBLC6-2 uses rail-to-rail protection. Its effectiveness depends on low inductance in all three branches, not just the signal trace:

```text
Positive D-line strike: J1 D± → D1 I/O → D1 VBUS (pin 5) → C2 local VBUS decoupling → GND plane
Negative D-line strike: J1 D± → D1 I/O → D1 GND (pin 2) → short, via-rich GND plane return
```

DS4260 warns that the data-I/O, VBUS and GND paths must all be as short as possible; its example uses `C_BUS = 100 nF`. C2 has the required nominal value on the same +5 V net, but its final position and the D1.2 GND-via geometry are unimplemented until Phase 3B. Do not place a long trace between D1.5 and C2, and do not use a thin or thermal-relieved D1.2 ground connection as the ESD return.

The USB shell is intentionally a separate `USB_SHIELD` net. It reaches system GND only through R3, a 0 Ω DNP option. D1.2 must connect directly to the continuous system GND reference plane; it is not a substitute for, nor required to be routed through, the optional shell bond. The shell stakes and the selected shield-bond strategy belong at the connector entry and must not lengthen the D1 GND discharge path.

## Phase 3B placement and routing requirements

| Requirement | Source / rationale | Phase 3B evidence required |
| --- | --- | --- |
| Place D1 immediately behind J1, before the protected trace runs toward U1. | ST AN5686: connector → TVS → IC; avoid the TVS-path inductance created by a tee/stub. | D1 is closest component on D+/D− path; routed view shows through-routing, no branch. |
| Make the J1-to-D1 segment as short/direct as placement allows. | The user-accessible connector is the ESD injection point; ST requires minimum I/O inductance. No official numeric distance was found, so none is invented. | Measured board distance and review screenshot. |
| Place C2 100 nF at D1.5/GND with the shortest practical VBUS/GND loop. | DS4260 Figure 17 / p. 5 rail-to-rail layout requirement. | D1.5, C2 and short GND return visible in PCB review. |
| Put D1.2 on a solid GND region with multiple, very close GND vias; avoid plane slots. | ST AN5686 recommends multiplying TVS GND vias; DS4260 identifies GND inductance as clamp-voltage error. | Via count/locations, stack-up and uninterrupted GND plane. |
| Route D+/D− as one 90 Ω differential pair, parallel and equal length. | Espressif USB guideline: 90 Ω ±10%, parallel/equal length. | Stack-up calculation and DRC impedance/length report. |
| Avoid stubs, splits and unnecessary layer changes. | ST through-TV S routing and Espressif reflection guidance. | Pair is routed J1 → D1 → U1 continuously; no testpoint tee; no unreviewed branches. |
| If a pair changes layers, add a pair of nearby GND return vias at every transition. | Espressif USB guideline. | Each differential via transition shown with adjacent GND return-via pair. |
| Maintain a continuous GND reference layer beneath the pair and ground copper around it. | Espressif USB guideline. | No plane split/void; pair clearance and return path reviewed. |
| Derive pair spacing, trace width and allowed length mismatch from the chosen stack-up. | Espressif specifies impedance and equal length, but does not give a universal geometry. | Fabricator stack-up/field-solver rule; no guessed geometry in this audit. |
| Keep D+/D−, D1 and their return path outside the ESP32 antenna keepout and away from the antenna. | Espressif calls for USB port, USB signal traces/vias/testpoints to be as far from the antenna as possible. | Antenna exclusion geometry and placement review. |
| Keep the pair and its return path away from U2 SW node, L1 and their high-di/dt loops. | Project AP63203/L1 audits require separation of USB from buck switching/magnetic region. | Physical separation and no shared disrupted GND return path. |
| Keep CC1/CC2 and VBUS routing out of the controlled D+/D− pair corridor. | Avoids crosstalk and impedance discontinuity; exact spacing is stack-up-dependent. | Copper-clearance rules and D+/D− impedance review. |
| Keep the shield termination at the connector entry; maintain `USB_SHIELD` as a separately reviewed net. | Present R3 DNP shield option; prevents accidental use of shell path as D1 return. | Shell stakes, R3 option and chassis/system-GND policy reviewed. |
| Reserve optional chip-side USB tuning positions. | Espressif recommends 22/33 Ω series and D+/D− shunt-cap footprints close to the chip. Current topology has neither. | **VERIFY / design decision:** document why no tuning footprints are present, or create a separate approved schematic-change task. This audit does not change it. |

## Footprint, 3D, assembly and procurement

| Topic | Finding | Status |
| --- | --- | --- |
| Pin-numbered footprint | KiCad 9 `Package_TO_SOT_SMD:SOT-23-6` has pads 1–3 at x = –1.1375 mm and 4–6 at x = +1.1375 mm, 0.95 mm pitch, and a visible pad-1 triangle. The mapping supports the required through-path. | ACCEPT |
| Part-specific land/stencil | DS4260 provides a SOT23-6L footprint recommendation. The assigned KiCad footprint is generic JEDEC SOT-23-6; direct overlay, paste/mask and assembler land-rule confirmation have not been performed. | VERIFY |
| 3D model | Footprint references `${KICAD9_3DMODEL_DIR}/Package_TO_SOT_SMD.3dshapes/SOT-23-6.step`. Its availability/orientation was not visually checked in the isolated environment. | VERIFY |
| Assembly | SOT23-6L is an ordinary SMT package, but reflow profile, polarity recognition, paste/mask and CPL rotation must be verified with the chosen assembler. | VERIFY |
| Procurement / PCBA | LCSC property is `C7519`; live LCSC/JLC stock, Basic/Extended class and PCBA eligibility were not captured in this audit. | VERIFY; manufacturing release BLOCKED |

## Disposition

| Item | Classification | Reason |
| --- | --- | --- |
| USBLC6-2SC6 identity, package and pin mapping | ACCEPT | ST pinout and current D1 symbol/footprint/net assignment agree. |
| D+ / D− end-to-end mapping | ACCEPT | J1, D1 and U1.27/U1.26 preserve D+/D−. |
| D1 VBUS and GND electrical topology | ACCEPT | Pin 5 is +5 V with 100 nF C2 on that rail; pin 2 is GND. |
| Connector-side D1 position, through routing and no stubs | VERIFY | PCB does not yet exist. |
| ESD current return loop, D1.2 vias and D1.5/C2 placement | VERIFY | Requires Phase 3B geometry and stack-up. |
| USB 90 Ω ±10%, length/spacing, layer transitions and GND reference | VERIFY | Requires fabricator stack-up and routed PCB. |
| Optional chip-side USB tuning footprint omission | VERIFY | Espressif recommends reserving them; absent positions need an explicit later design decision. |
| Footprint 3D/stencil/assembly and JLCPCBA procurement | VERIFY | Not confirmed here. |
| Manufacturing release | BLOCKED | PCB layout/DRC, return-path review, assembly/procurement checks and prototype USB/ESD validation remain. |

### Change decision and Phase 3B gate

There is **no CHANGE** to the schematic, generator, footprint or BOM from this audit. The electrical protection topology is correct. Phase 3B may place and route the USB path only after reserving D1 directly at J1, C2 directly at D1.5/GND, a continuous GND return, and the antenna/buck exclusions above. Manufacturing release remains **BLOCKED** until those rules are implemented and reviewed.

No commit or push was performed.
