# ESP32-C3-MINI-1-H4X footprint / EPAD / antenna監査

**監査日:** 2026-08-03 JST
**対象:** U1 `ESP32-C3-MINI-1-H4X`のみ。部品識別、symbol-to-footprint番号、2D land pattern、EPAD、antenna keepout、残るPCB／assembly gateを扱う。これは監査のみの記録であり、generator、回路図、footprint、PCB、BOM/CPL、Gerber fileを**変更しない**。

## 出典

| Source | Revision / date | Used for |
| --- | --- | --- |
| [ESP32-C3-MINI-1 & ESP32-C3-MINI-1U Datasheet](https://documentation.espressif.com/esp32-c3-mini-1_datasheet_en.pdf) | v2.2, 2026-05-06; retrieved 2026-08-03 JST | Table 1-1, Table 3-1, Figures 3-1, 9-1, 10-1 and 11-1 (pp. 3, 10–11, 34–35, 38). |
| [ESP32-C3 Hardware Design Guidelines — PCB Layout](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32c3/pcb-layout-design.html) | latest, retrieved 2026-08-03 JST | Module positioning, EPAD/via practice, antenna/base-board and enclosure clearance. |
| [Espressif KiCad Library](https://github.com/espressif/kicad-libraries) | retrieved 2026-08-03 JST | Espressif publishes a symbol, footprint and 3D-model library for ESP32-C3-MINI-1. |
| Project local library | `symbols/Espressif.kicad_sym`, `footprints/Espressif.pretty/ESP32-C3-MINI-1.kicad_mod`; read 2026-08-03 JST | Actual symbol and footprint assigned by this project. |

The official datasheet makes its Figure 11-1 land-pattern drawing and source/STEP data the controlling reference. Dimensions not made unambiguous by the accessible drawing or text are not inferred below.

## 現行設計の抽出

| Field | Current formal schematic / generator value | Assessment |
| --- | --- | --- |
| Reference / value | `U1` / `ESP32-C3-MINI-1-H4X` | ACCEPT |
| Manufacturer part number | `Espressif ESP32-C3-MINI-1-H4X` | ACCEPT |
| LCSC property | `C41349510` | VERIFY — property exists; live LCSC/JLC availability and PCBA eligibility were outside this audit. |
| Symbol | `Espressif:ESP32-C3-MINI-1` | ACCEPT — all pins 1–53 are present. |
| Formal footprint assignment | `Espressif:ESP32-C3-MINI-1` | ACCEPT for 2D assignment. `fp-lib-table` resolves `Espressif` to the project-local official footprint directory. |
| Actual footprint file | `hardware/pcb/esp32-c3-main/footprints/Espressif.pretty/ESP32-C3-MINI-1.kicad_mod` | Audited directly; header is `(version 20221018) (generator pcbnew)`. |

`H4X` is not a vague suffix: Table 1-1 lists it as a **Recommended** PCB-antenna module with 4 MB Quad-SPI flash, chip revision v1.1, ambient range –40 to 105 °C, and 13.2 × 16.6 × 2.4 mm body size. The datasheet identifies the embedded chip as ESP32-C3FH4X (v1.1, 4 MB). The older `-H4` is NRND; no substitution is authorized by this audit.

## symbol / pad番号mapping監査

The datasheet states that the module has 53 pins. Direct parsing of the current symbol gives exactly one pin each for 1 through 53. Direct parsing of the footprint gives pads 1–48 and 50–53 once each, plus nine copper/paste segments all numbered 49. KiCad therefore treats the nine segments as one logical EPAD, not as nine different electrical pins.

| Official function / pin numbers | Current symbol | Current footprint | Current schematic treatment | Result |
| --- | --- | --- | --- | --- |
| GND: 1, 2, 11, 14, 36–53 | `GND` | Same pad numbers, including EPAD 49 | All labelled `GND` | ACCEPT |
| 3V3: 3 | `3V3` | Pad 3 | `+3V3` | ACCEPT |
| NC: 4, 7, 9, 10, 15, 17, 24, 25, 28, 29, 32–35 | `NC` | Same pad numbers | No-connect markers | ACCEPT |
| GPIO2: 5; GPIO3: 6; EN: 8 | Same official functions | Pads 5, 6, 8 | 5 NC, 6 `STATUS_LED`, 8 `EN` | ACCEPT |
| GPIO0/1: 12/13; GPIO10: 16; GPIO4/5: 18/19 | Same official functions | Same pad numbers | No-connect markers | ACCEPT |
| GPIO6/7: 20/21; GPIO8/9: 22/23 | Same official functions | Same pad numbers | `I2C_SDA`, `I2C_SCL`, 22 NC, `BOOT` | ACCEPT |
| USB: GPIO18/19 = 26/27 | Same official functions | Same pad numbers | `USB_D-` / `USB_D+` | ACCEPT |
| UART0: RXD0/TXD0 = 30/31 | Same official functions | Same pad numbers | `UART_RX` / `UART_TX` | ACCEPT |

This table verifies pad-number correspondence only; it does not approve final trace placement. The two existing U1 warnings attributed to the symbol/footprint library default (`PCM_Espressif` in the symbol versus the project-local `Espressif` assignment) are not a numbered-pad mismatch and are outside the no-change scope of this audit.

## 2D footprint and EPAD audit

| Item | Official reference | Actual local footprint | Result / required action |
| --- | --- | --- | --- |
| Module body | 13.2 ±0.15 × 16.6 ±0.15 × 2.4 ±0.15 mm, Figure 10-1 | F.Fab body from x = –6.6 to +6.6 and y = –8.3 to +8.3 mm: 13.2 × 16.6 mm | ACCEPT |
| Castellated signal pads | 48 pads, 0.8 mm pitch; Figure 11-1 gives the 0.4 mm pad dimension | 48 SMD pads 1–48, 0.4 × 0.8 mm; 0.8 mm pitch along each side | ACCEPT |
| Four additional GND pads | Four 0.7 mm pads, Figure 11-1 | Pads 50–53, each 0.7 × 0.7 mm, all `GND` in symbol and schematic | ACCEPT |
| Central exposed pad | Pin 49 is `EPAD` in Figure 9-1 and is GND in Table 3-1 | Nine coplanar SMD segments all numbered 49; eight are 1.45 × 1.45 mm and the antenna-side corner segment is custom-shaped to remain inside the outline | ACCEPT for numbering and 2D copper/paste segmentation |
| EPAD solder paste | Datasheet: EPAD soldering is optional for thermal optimization; if soldered, use the correct amount because excess paste can lift the module. Hardware guide: use a square grid, apply paste over gaps, and place GND vias in gaps. | Every logical-pad-49 segment has F.Cu/F.Paste/F.Mask; segmentation leaves gaps between tiles. No vias are embedded in the library footprint. | VERIFY in Phase 3B — choose stencil aperture reduction and via tenting/fill policy with the assembler. Do not remove/rename pad 49. |
| Thermal vias | Figure 11-1 marks thermal-pad vias. Accessible official text does not establish a module-specific via count or drill diameter. | None in the footprint, appropriately: vias belong to the board implementation. | VERIFY in Phase 3B — use a GND-via grid in EPAD gaps only after stack-up/fabricator rules are selected. Do not claim a count or diameter without the official CAD/reference design and fabricator confirmation. |
| Silkscreen / courtyard | Official drawing supplies body/land information; it does not replace assembly clearance analysis. | F.CrtYd is x = –6.8…+6.8, y = –8.5…+8.5 mm (13.6 × 17.0 mm); F.SilkS marks the body and `Antenna Area`. | ACCEPT as a component courtyard; it is **not** the antenna or cable/enclosure keepout. |

The hardware guideline’s “at least nine GND vias” statement applies to the bare ESP32-C3 chip ground pad. It is not used here as an invented numeric requirement for the module EPAD. For a module EPAD, the same guideline instead specifies a paste-covered grid with GND vias in the gaps.

## antennaとbase-board keepoutの監査

| Item | Official requirement | Current footprint | Status |
| --- | --- | --- | --- |
| Antenna location | ESP32-C3-MINI-1 has an on-board PCB antenna; Figure 11-1 marks a 5.4 mm antenna area at one end of the 16.6 mm module. | The footprint marks `Antenna Area` from y = –8.3 to –2.9 mm, x = –6.6 to +6.6 mm: 13.2 × 5.4 mm. | ACCEPT |
| Under-module antenna keepout | Datasheet Figure 3-1 identifies the dotted antenna zone. | Named `antenna keepout` polygon exactly covers that 13.2 × 5.4 mm area on `*.Cu`; tracks, vias, pads, copper pours and footprints are forbidden. | ACCEPT — all copper layers are covered by the local zone. |
| Board-edge placement | Prefer antenna outside the base board and feed point close to its edge. If it cannot overhang, place feed near the edge and cut the base board on both sides of and below the antenna; do not place the module in board center with a four-sided hollow. | No Edge.Cuts or external clearance is embedded in the component footprint. | VERIFY / Phase 3B — choose the module orientation and board outline. |
| External clearance | If the antenna cannot be outside the board, the guideline calls for at least 15 mm clearance in all directions around the antenna area, with no copper, routing or components; preserve sufficient nearby ground copper and dense GND vias where the guidance permits. | The 5.4 mm local zone is only the module-underbody keepout; it does not encode this base-board/enclosure rule. | CHANGE required in the **future PCB layout**, not in this footprint audit: define the external all-layer copper/object keepout and mechanism rule on the PCB. |
| Enclosure / metal / cables | End-product housing must retain at least 15 mm antenna clearance in all directions. | Not represented before PCB/enclosure design. | VERIFY / Phase 3B mechanical review. |
| USB, buck L1 and SW node | No official module-specific numeric spacing was found. RF-sensitive antenna area must remain clean; the project’s USB and buck audits already require their routing/switching region to stay outside the antenna keepout. | No PCB placement exists yet. | VERIFY / Phase 3B — do not route USB or place U2/L1/SW copper/return currents inside the 15 mm antenna/mechanical clearance; obtain RF validation from Rev.A. |

## 3D, assembly and procurement

| Topic | Finding | Status |
| --- | --- | --- |
| 3D model reference | The footprint references `${KICAD7_3RD_PARTY}/3dmodels/com_github_espressif_kicad-libraries/espressif.3dshapes/ESP32-C3-MINI-1.STEP`, offset (–6.6, –8.37, 0), rotation (0, 0, 0). The datasheet says Espressif supplies a STEP model. | VERIFY — `KICAD7_3RD_PARTY` is unset and no matching STEP/WRL was found in the project or installed KiCad paths, so origin/orientation could not be visually verified in this environment. |
| GUI / 3D inspection | Codex’s isolated environment cannot operate the user’s KiCad GUI. | VERIFY — open the formal schematic/footprint and 3D viewer during Phase 3B to confirm pin-1 and antenna-end orientation. |
| Assembly handling | Datasheet specifies MSL 3 and a 168-hour floor-life limit after opening at 25 ±5 °C / 60%RH before bake is required. | VERIFY — carry this into assembler instructions and final PCBA process review. |
| Procurement / JLCPCBA | Current formal property is LCSC `C41349510`; no live LCSC/JLC part page, stock, feeder, placement, or PCBA eligibility was verified in this audit. | VERIFY; manufacturing release BLOCKED. |

## 判定とPhase 3B gate

| Item | Classification | Reason |
| --- | --- | --- |
| Exact H4X part identity, body, 53-pin mapping | ACCEPT | Official v2.2 datasheet and current U1 agree. |
| Current `Espressif:ESP32-C3-MINI-1` 2D footprint assignment | ACCEPT | Project library table resolves it to the directly audited local footprint; numbers, 0.8 mm pitch, body, GND pads and EPAD number agree. |
| EPAD physical paste/via implementation | VERIFY | Board-specific stencil and via design are not footprint-library data; official module-specific via count/drill was not available in text. |
| Antenna zone inside component footprint | ACCEPT | All-layer copper/object keepout is present for the official 13.2 × 5.4 mm antenna area. |
| Board edge, external all-layer clearance, enclosure/metal and buck/USB separation | CHANGE in future PCB layout / VERIFY now | Required Phase 3B design work, not a schematic or footprint-file change. |
| 3D model availability/orientation | VERIFY | Referenced official model is unavailable in this environment. |
| JLCPCB procurement and placement eligibility | VERIFY | No live official procurement/PCBA evidence captured. |
| Manufacturing release | BLOCKED | Requires completed PCB implementation/DRC, antenna/mechanical/RF review, 3D confirmation, and JLC/assembler/procurement validation. |

### footprint変更の決定

**No footprint-file change is justified by this audit.** The local 2D footprint and its symbol mapping are accepted. The required next changes are PCB-only: apply the external antenna/board-edge keepout, decide EPAD paste/via strategy, perform DRC and 3D/mechanical review, and then validate the actual assembled RF performance. This audit does not authorize a change to the generator, formal schematic or footprint library.

### Phase 3Bで進めてよい事項

- placing U1 with the antenna end at the selected board edge/outside the board;
- creating the external antenna clearance and mechanical/enclosure keepout before other placement;
- creating the EPAD GND-via/paste design after fabrication/assembly rules are known; and
- DRC, 3D, antenna and prototype RF verification.

Manufacturing release remains **BLOCKED**. No commit or push was performed.
