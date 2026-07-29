# SEN66 と ESP32 DevKitC の配線

SEN66は **3.3 V専用** で接続します。5 Vには接続しないでください。

| SEN66 | 信号 | ESP32 DevKitC |
| --- | --- | --- |
| Pin 1（赤） | VDD | 3V3 |
| Pin 2（黒） | GND | GND |
| Pin 3（緑） | SDA | GPIO 21 |
| Pin 4（黄） | SCL | GPIO 22 |
| Pin 5 | NC | 接続しない |
| Pin 6 | NC | 接続しない |

初期設定のI2Cアドレスは`0x6B`、周波数は100 kHzです。GPIO、周波数、アドレスは
[`src/app_config.h`](../src/app_config.h)で一元管理しています。ESP32ボードにGPIO 21/22がない場合は、
そのファイルだけを変更してください。

配線確認時は、SDA/SCLの逆接続、GNDの未接続、および5 V給電を特に確認してください。
