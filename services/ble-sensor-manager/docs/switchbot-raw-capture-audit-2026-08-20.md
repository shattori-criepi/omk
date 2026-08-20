# SwitchBot BLE raw capture監査（2026-08-20）

## 目的と適用範囲

2026-08-19、SwitchBot CO2センサーのmanufacturer packetで、少数capture時に
固定と見えたbyteをclassifierに使ったため、有効な広告をdecoderが拒否した。この文書は、
同じ問題を他機種で繰り返さないために、2026-08-20に所有実機から採取したraw
advertisementの観測結果を記録するものである。

対象はMotion、防水温湿度、Meter、Plugである。ここに記載する「安定」は観測範囲内の
事実であり、SwitchBotの全firmware・全個体での仕様保証を意味しない。MACアドレスは
captureを同一物理個体として対応付けるためだけに記載し、decoder classifierには使わない。

## 根拠レベル

decoder条件を追加・変更する際は、各byteの根拠を次のように区別する。

| 区分 | 意味 |
| --- | --- |
| protocol / official documentation | 公開protocolまたは公式仕様に根拠がある |
| 複数個体・複数capture | 個体差と時間変化の両方を一部確認している |
| 1個体・複数packet | 同一個体での時間変化は確認したが、個体差は未確認 |
| 注意が必要な値 | counter、状態、測定値、reserved、終端らしき値。固定classifierに使う前に追加根拠が必要 |

未知のSwitchBot機器を既知機種と誤認するより未識別として扱うことを優先する。ただし、
CO2問題のように実測で可変と分かったbyteを固定し、有効packetを落とさないことも同じく重要である。

## CO2問題からの前提

旧CO2 decoderは`index 7 == 0xe4`と`index 11 == 0`を要求していた。2個体での追加captureでは
これらに加えindex 6と12も可変と判明した。現行CO2 decoderは次だけを用いる。

- manufacturer dataが16 byte
- `data[15] == 0`
- `data[8:11]`が妥当なMeter互換温湿度
- `data[13:15]`がbig-endianで400〜10,000 ppm

index 6、7、11、12はclassifier条件にしない。CO2は2個体・6 captureで確認済みだが、
`data[15] == 0`も観測上の終端であって、全個体への仕様保証ではない。

## 実機監査

### Motion

- 対象: `motion-001`（`switchbot:cffc6a48db15`）
- 根拠レベル: 1個体・複数packet
- manufacturer data: 10 byte

代表packet:

```text
cf fc 6a 48 db 15 36 2c 00 59
cf fc 6a 48 db 15 37 6c 00 01
cf fc 6a 48 db 15 38 2c 00 21
cf fc 6a 48 db 15 39 6c 00 00
cf fc 6a 48 db 15 3a 2c 00 46
cf fc 6a 48 db 15 3b 6c 00 00
```

| byte / 条件 | 観測 | decoder判断への含意 |
| --- | --- | --- |
| length | 常に10 byte | 現行length条件と整合 |
| index 6 | `36`〜`3b`へ連続変化 | classifierに使わない |
| index 7 | `2c` / `6c` | bit 6のmotion state解釈と整合 |
| `index 7 & 0x3f` | 常に`0x2c` | 現行mask条件と整合。ただし1個体のみ |
| index 8 | 常に`00` | 現行reserved条件と整合。ただし仕様保証ではない |
| index 9 | 継続的に変化し、resetも観測 | classifierに使わない |

現行decoderは変更しない。index 7下位6 bitとindex 8を緩和する場合は、別個体または
firmware差のcaptureを先に得る。

### 防水温湿度

- 対象: `th-002`（`switchbot:d66917d31038`）、`waterproof_sensor`
- 根拠レベル: 1個体・56 packet（3分間）
- manufacturer data: 12 byte
- service data: SwitchBot service UUID `fd3d`、`770047`

代表packet:

```text
d6 69 17 d3 10 38 d2 0b 01 9d ca 00
d6 69 17 d3 10 38 d3 0b 02 9d ca 00
```

| byte / 条件 | 観測 | decoder判断への含意 |
| --- | --- | --- |
| length | 常に12 byte | 現行length条件と整合 |
| index 6 | `d2` / `d3` | classifierに使わない |
| index 7 | `0b` | 1個体での観測に留まる |
| index 8 | `01` / `02` | 温湿度値の一部として可変 |
| index 9 / 10 | `9d` / `ca` | 温湿度値としてdecode |
| index 11 | 56 packetすべて`00` | 現行条件の実測根拠。ただしreservedらしき注意値 |

防水温湿度とPlugはいずれも12 byte manufacturer layoutである。index 11を安易に緩和すると
collisionの評価が必要になるため、現行decoderは変更しない。index 11は1個体の観測根拠であり、
仕様保証として記述・利用しない。

### Meter

- 対象: `th-001`（`switchbot:cf3941c7ed79`）、`temperature_humidity_sensor`
- 根拠レベル: 1個体・117 packet（3分間）
- manufacturer data: 11 byte
- service UUID / service data: 今回のcaptureではなし

通常設置場所ではESP32 BLE relay経由だったため、実機を一時的にGateway付近へ移動して直接captureした。

代表packet:

```text
cf 39 41 c7 ed 79 39 03 06 98 38
cf 39 41 c7 ed 79 3a 03 06 98 35
cf 39 41 c7 ed 79 3b 03 08 98 35
cf 39 41 c7 ed 79 3c 03 00 99 35
cf 39 41 c7 ed 79 3d 03 00 99 33
```

| byte / 条件 | 観測 | decoder判断への含意 |
| --- | --- | --- |
| length | 常に11 byte | 現行length条件と整合 |
| index 6 | `39`〜`3d`へ変化 | classifierに使わない |
| index 7 | 117 packetすべて`03` | 現行marker条件の実測根拠。ただし1個体のみ |
| index 8 | `00` / `06` / `08` | 温湿度値の一部として可変 |
| index 9 | `98` / `99` | 温湿度値の一部として可変 |
| index 10 | `33` / `35` / `38` | 温湿度値の一部として可変 |

現行decoderは変更しない。`index 7 == 0x03`は今回の1個体では安定したが、将来緩和または
強化する根拠としては別個体captureが必要である。

### Plug

- 対象: `plug-001`（`switchbot:ac276e43269e`）
- 根拠レベル: 1個体・2,037 packet（3分間）。別個体の既存captureも参照
- manufacturer data: 12 byte
- service data: SwitchBot service UUID `fd3d`、`6a0064`

代表packet:

```text
ac 27 6e 43 26 9e 35 80 10 36 00 29
ac 27 6e 43 26 9e 36 80 10 35 00 29
```

| byte / 条件 | 観測 | decoder判断への含意 |
| --- | --- | --- |
| length | 常に12 byte | 現行length条件と整合 |
| index 6 | `35` / `36` | classifierに使わない |
| index 7 | `80` | `index 7 & 0x7f == 0`と整合 |
| index 8 | 今回の個体は`10` | Plug全体の固定markerにしない |
| index 9 | `35` / `36` | classifierに使わない |
| index 10 / 11 | `00` / `29` | 電力値としてdecode |

既存の別個体captureではindex 8が`16`であり、個体差がある。現行実装はservice dataがあるときは
index 8を使わず、serviceなしmanufacturer-only packetに限り誤認防止のstrict fallbackとして
`0x16` markerを使う。今回の3分captureはすべてservice UUID付きであり、serviceなしpacketにおける
marker妥当性は未検証である。現行decoderは変更せず、serviceなしpacketを得たときに再監査する。

## 将来のdecoder変更時の確認事項

1. 単一packetで一定だったbyteを、機種識別markerとみなさない。
2. counter、状態、測定値、reserved、終端らしき値をclassifierに使う場合、根拠レベルを明記する。
3. 同一個体では複数packetと状態遷移を観測し、可能なら複数個体でも確認する。
4. packet lengthだけで別機種を識別しない。特に防水温湿度とPlugの12 byte collisionを評価する。
5. MACアドレスをclassifierに使わない。
6. 緩和前に既知decoderとのcollisionと未知SwitchBot機器の誤認を評価する。
7. 不確実なpacketは既知機種に推測decodeせず、raw付きunknown candidateとして残す。
8. 可変と実測されたbyteを固定し、有効packetを拒否しない。

追加captureは次で取得できる。capture時刻、対象個体、状態操作の有無もfixtureまたは監査文書へ残す。

```sh
PYTHONPATH=src .venv/bin/python -m omk_ble.raw_scan --seconds 180
```
