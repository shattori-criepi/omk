# Murata MLCC DC-bias監査 — C1、C4、C5

Audit date: 2026-08-03 JST. Scope is the effective capacitance of C1, C4 and C5 in the AP63203 supply only. This document is an audit record; it does **not** change the generator, formal schematic, PCB, footprint, BOM/CPL or Gerber.

## 公式出典と測定条件

| Source | Retrieval / status | Use |
| --- | --- | --- |
| [Murata SimSurfing — GRM21BR61A106KE19L](https://ds.murata.com/simsurfing/mlcc.html?oripartnumbers=%5B%22GRM21BR61A106KE19L%22%5D&partnumbers=%5B%22GRM21BR61A106KE19%22%5D) | Official query and its public `characteristics` API retrieved 2026-08-03 JST | C-DC bias and C-temperature curves for C1. |
| [Murata SimSurfing — GRM21BR60J226ME39L](https://ds.murata.com/simsurfing/mlcc.html?oripartnumbers=%5B%22GRM21BR60J226ME39L%22%5D&partnumbers=%5B%22GRM21BR60J226ME39%22%5D) | Official query and its public `characteristics` API retrieved 2026-08-03 JST | C-DC bias and C-temperature curves for C4/C5. |
| [Murata MLCC part-number list](https://www.murata.com/-/media/webrenewal/tool/library/common-pdf/static-model/component-list-s-mlcc-2506.ashx) | Murata list, retrieved 2026-08-03 JST | Nominal value, rated voltage, X5R, 0805, tolerance and temperature range. |
| [Diodes AP63200/AP63201/AP63203/AP63205 datasheet](https://www.diodes.com/datasheet/download/AP63200-AP63201-AP63203-AP63205.pdf) | DS41326 Rev. 3-2, November 2024 | AP63203 Table 2, input-capacitor and output-capacitor guidance. |

SimSurfing data used here are **representative characteristic data**, not a production guaranteed minimum: 25°C, AC 0.5 Vrms, the stated DC bias, and the listed part-number family without the tape suffix. The official API reports available temperature points −55°C, 25°C and +85°C. Values below are rounded to two or three meaningful figures; no measured production worst case is inferred.

## 現行設計の抽出

| Ref. | Formal design / use | MPN and nominal specification | Applied DC voltage |
| --- | --- | --- | --- |
| C1 | `+5V` to GND, with C2 100 nF, at U2 VIN input | Murata `GRM21BR61A106KE19L`; 10 uF, 10 V, X5R, 0805, ±10% | 5.0 V |
| C4 | `+3V3` to GND, after L1 at AP63203 output / FB sense | Murata `GRM21BR60J226ME39L`; 22 uF, 6.3 V, X5R, 0805, ±20% | 3.3 V |
| C5 | Same net and part as C4, in parallel | Murata `GRM21BR60J226ME39L`; 22 uF, 6.3 V, X5R, 0805, ±20% | 3.3 V |

The generator and formal schematic agree on these MPNs, values and nets. C1/C4/C5 have standard `Capacitor_SMD:C_0805_2012Metric` footprints. No formal circuit file was edited for this audit.

## 公式DC-bias結果

| Ref. / MPN | Nominal capacitance | SimSurfing DC-bias condition | Representative effective capacitance | Nominal-retention view | Temperature points at applied DC bias |
| --- | ---: | --- | ---: | ---: | --- |
| C1 `GRM21BR61A106KE19L` | 10 uF ±10% | 25°C, AC 0.5 Vrms, DC 5.0 V | **about 5.28 uF** | about 53% of nominal | −55°C: about 4.62 uF; +25°C: about 5.28 uF; +85°C: about 5.15 uF |
| C4 or C5 `GRM21BR60J226ME39L` | 22 uF ±20% | 25°C, AC 0.5 Vrms, DC 3.3 V | **about 11.7 uF each** | about 53% of nominal | −55°C: about 11.1 uF; +25°C: about 11.7 uF; +85°C: about 10.8 uF |
| C4 + C5 parallel | 44 uF nominal | Same as each individual part | **about 23.5 uF total** | about 53% of nominal total | −55°C: about 22.1 uF; +25°C: about 23.5 uF; +85°C: about 21.6 uF |

The C4/C5 total is the sum of two equal representative curves. It is not a separately guaranteed two-part minimum. Temperature curves describe the individual selected characteristic at the stated DC/AC condition. Tolerance, DC bias and temperature must not be multiplied into a claimed “official worst case” because Murata has not supplied a combined guaranteed lower-bound data set in the retrieved material.

### C1入力の評価

AP63203 Table 2 specifies **10 uF nominal** input capacitance. The input-capacitor text separately states that a ceramic capacitor **greater than 10 uF** is sufficient for most applications; these are not the same claim. The 5 V representative value of about 5.28 uF is below the Table-2 nominal value. C2's 100 nF is an EVM-style high-frequency bypass and does not establish 10 uF of bulk effective capacitance.

The datasheet does not publish an AP63203 minimum effective-CIN stability threshold for this exact USB/PTC source impedance and load transient. Therefore the data do not prove the current C1 is sufficient, but also do not prove it must be replaced. C1 is **VERIFY**, not ACCEPT or CHANGE. Before manufacturing release, select/verify the input source impedance and startup transient, and either establish adequate margin by test/calculation or revise the capacitor selection in a separate authorized implementation task.

### C4/C5出力の評価

AP63203 Table 2 and the WU-EVM use two 22 uF ceramic capacitors. Datasheet output guidance says 22–68 uF ceramic is sufficient for most applications and calls for large capacitance/low ESR for load transient response. At 25°C and 3.3 V, the current pair's representative total is about 23.5 uF, inside that 22–68 uF guidance. The SimSurfing temperature curve yields about 22.1 uF at −55°C and about 21.6 uF at +85°C before any separate tolerance treatment.

Thus the topology is nominally aligned but has little representative margin against the 22 uF lower guidance at high temperature. Because the retrieved official data do not provide a combined DC-bias/tolerance/temperature guaranteed minimum or an AP63203 output-capacitance stability floor, C4/C5 are **VERIFY**, not ACCEPT. This is not evidence of a required schematic change yet.

## AP63203との比較と判定

| Item | Official comparison | Decision | Rationale |
| --- | --- | --- | --- |
| C1 nominal topology | Table 2: 10 uF; EVM also 10 uF plus 100 nF | VERIFY | Nominal match, but 5.0 V representative effective value is about 5.28 uF and datasheet wording also says >10 uF for most applications. |
| C1 rating / temperature family | 10 V, X5R, 0805, ±10%, −55 to +85°C | ACCEPT for identity | Applied 5.0 V is below 10 V. Effective-capacitance adequacy remains VERIFY. |
| C4/C5 nominal topology | Table 2/EVM: 2 × 22 uF; general output guidance 22–68 uF | VERIFY | 25°C representative total about 23.5 uF is in range, but +85°C representative total about 21.6 uF is slightly below the guidance lower end before tolerance. |
| C4/C5 rating / temperature family | 6.3 V, X5R, 0805, ±20%, −55 to +85°C | ACCEPT for identity | Applied 3.3 V is below 6.3 V. Effective-capacitance adequacy remains VERIFY. |
| Circuit values / parts | No official data establishes a mandatory alternative value/rating for this 5 V USB source | No CHANGE | Any capacitance/voltage-rating change must be a separate approved implementation change after required margin is defined. |
| Manufacturing release | MLCC effective-capacitance margin and Phase 3B layout/transient verification open | BLOCKED | The representative data remove the prior data-access blocker, but do not prove worst-case AP63203 input/output margin. |

## 状態一覧

### ACCEPT

- Official DC-bias data were obtained for the exact C1 and C4/C5 part-number families.
- Part identities, nominal ratings, use voltages and formal schematic connections are consistent.
- C4/C5's 25°C representative parallel total is within AP63203's 22–68 uF general output guidance.

### VERIFY

- C1 adequacy at 5 V: representative effective capacitance is about 5.28 uF, not 10 uF.
- C4/C5 worst-case effective output capacitance, load-transient response and stability margin.
- MLCC tolerance combined with DC bias/temperature; only separate official characteristic information was retrieved.
- Physical placement/return loop, ESR at switching conditions, USB/PTC source impedance and startup/load testing in Phase 3B/prototype.
- Procurement/lifecycle confirmation remains an order-time check; official list inclusion is not treated as a lifetime supply guarantee.

### CHANGE

None. The audit does not authorize or require a circuit change from representative DC-bias data alone.

### BLOCKED

- Manufacturing release is blocked until the VERIFY items have a documented design margin or are resolved through an authorized component/topology revision and verification.

## Phase 3B gate

Phase 3B may proceed with the current electrical topology for preliminary placement/routing. Keep C1/C2 at U2 VIN/GND and C4/C5 at the post-L1 output/FB return as required by the AP63203 layout guidance. Do not freeze the manufacturing BOM or release fabrication/assembly data until C1/C4/C5 effective-capacitance margins, thermal/transient behavior and procurement status are closed.

## リポジトリ確認

- `git diff --check`: passed for tracked files.
- `git diff --no-index --check /dev/null hardware/pcb/esp32-c3-main/murata-mlcc-dc-bias-audit.md`: passed (the expected no-index difference exit status was handled; no whitespace diagnostics).
- This audit adds only this Markdown file. `ap63203-pin-audit.md` remains the pre-existing untracked audit document.
- No commit or push was performed.
