# Phase 3A USB-C input/protection review — 2026-08-03 JST

Scope is J1, F1, D1, R1/R2, R3 and USB routing only; no schematic change.

## Connection audit

The formal schematic labels all J1 VBUS pins `+5V_USB`, all A1/A12/B1/B12 GND, A6/B6 `USB_D+`, A7/B7 `USB_D-`; A5 and B5 are independent CC nets through separate R1/R2 to GND. A8/B8 are NC. F1 separates VBUS to `+5V`; D1 is on the data nets before GPIO19 D+ / GPIO18 D−. D1 pin 1/6 is I/O1, 3/4 I/O2, pin 2 GND, pin 5 VBUS. R3 provides shield-to-GND 0 Ω DNP. TP10/TP11 are labelled USB_D+/USB_D− and must be implemented as inline pads or very short stubs in Phase 3B.

## J1

**Maintain JAE DX07S016JA1R1500 / LCSC candidate C3197885 as the electrical recommendation.** It is an active JAE 16-position USB 2.0 one-row SMT receptacle. The product page identifies drawing `SJ121837`, specification `JACS-30413` and handling instructions `JAHL-30353-1`, but their controlled contents were not anonymously retrievable on 2026-08-03 JST. The assigned KiCad footprint name exactly matches the MPN and its USB2 pad mapping is coherent; its official signal/shell/NPTH land geometry, board-edge datum, paste/mask and 3D/mechanical envelope remain VERIFY until those JAE records are obtained. JLCPCB publicly lists C3197885 as Extended / SMT Assembly / Economic and Standard / High assembly difficulty / MSL 1, but live order-flow, fixture and CPL status remain VERIFY. See [usb-c-j1-footprint-audit.md](usb-c-j1-footprint-audit.md); this audit makes no circuit or footprint change.

## CC

R1/R2 5.1 kΩ 1% 0402 are ACCEPT. A sink receptacle requires independent Rd on CC1 and CC2; do not join them. USB Type-C R2.0 and Renesas Type-C sink guidance identify 5.1 kΩ Rd. This advertises a 5 V sink; no PD/higher-current negotiation occurs. 0402 power dissipation is negligible. Existing Yageo RC0402FR-075K1L remains the preferred MPN; LCSC/JLC live listing VERIFY.

## F1

Bourns MF-MSMF110-2 is Electrical: ACCEPT, Thermal/high-temperature: VERIFY, Startup test: VERIFY, Procurement: VERIFY, Manufacturing release: BLOCKED. Official MF-MSMF data: 16 V max, 1.10 A hold, 2.20 A trip, 0.04 Ω initial / 0.21 Ω maximum resistance, 1812 footprint. The 0.85 A figure is 3.3 V output load, not normal F1 current: at 2.805 W and 88–93% efficiency, 5 V input current is about 0.60–0.64 A. At that 25°C design point it has current margin below 1.10 A; temperature derating, PTC voltage drop/heating, USB insertion inrush and simultaneous ESP32/SEN66 startup require hardware verification. Retain it for Rev.A short-circuit/field-fault protection; no 0 Ω or DNP option is needed unless startup testing trips it. Official: https://www.bourns.com/docs/product-datasheets/mf-msmf.pdf

## D1

**Maintain ST USBLC6-2SC6 / LCSC C7519.** It is an active USB 2.0 ESD part with two data channels plus VBUS protection, 3.5 pF max line capacitance, IEC 61000-4-2 level 4 (8 kV contact / 15 kV air), SOT23-6L package. It is suitable for Full Speed and has no D+/D− polarity when each paired I/O pin is connected as specified. Its GND return must be short and via-rich. Official: https://www.st.com/resource/en/datasheet/usblc6-2.pdf ; current ST product page: https://www.st.com/en/protections-and-emi-filters/usblc6-2.html . JLC class/live stock VERIFY. USBLC6-2P6 is package alternative only; no alternate is selected without an official LCSC/JLC listing review.

## Shield, VBUS and Phase 3B layout

For this plastic, unearthed enclosure, retain **R3 0 Ω DNP** as the Rev.A recommended tuning option; populate 0 Ω only after EMI/ESD evaluation. Do not hard-short shield before the chassis/ESD return strategy is measured. A 1–4.7 nF capacitor/RC option may be proposed after testing but needs a deliberate schematic change.

C1 10 µF + C2 100 nF are after F1 and are appropriate local buck input decoupling; USB inrush compliance still requires measurement with the final PTC, cable and host. Route D+/D− as ~90 Ω differential according to stackup, length-match, avoid vias/stubs/90° corners, place ESD at J1, keep continuous GND reference, and separate from SW/L1/antenna. Place shell stakes near GND vias; keep CC separate from the differential pair.

## Status

| Item | Electrical | Procurement | Footprint | PCB layout | Manufacturing release |
| --- | --- | --- | --- | --- | --- |
| J1 | ACCEPT | VERIFY | VERIFY (official `SJ121837` land/edge comparison pending) | CONDITIONAL GO for electrical planning; mechanical edge freeze VERIFY | BLOCKED |
| F1 | ACCEPT | VERIFY | ACCEPT | VERIFY | BLOCKED |
| D1 | ACCEPT | VERIFY | ACCEPT | VERIFY | BLOCKED |
| R1/R2 | ACCEPT | VERIFY | ACCEPT | VERIFY | BLOCKED |
| R3 | DNP OPTION | VERIFY | ACCEPT | VERIFY | BLOCKED pending ESD/EMI validation |
