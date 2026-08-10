# SEN66 と M5Stack AtomS3 Lite の配線

AtomS3 LiteとSEN66本体を直接配線しません。Adafruit SEN6x Breakoutを必ず介在させます。AtomS3 Lite Groveポートの5 VはBreakoutのVINへ入り、Breakout搭載のレギュレータとI2Cレベルシフタを通じてSEN66へ接続されます。SEN66本体へ5 Vを直接入力してはいけません。

| 接続区間 | 信号 | 接続先 |
| --- | --- | --- |
| AtomS3 Lite Grove Black | GND | Grove / STEMMA QT変換 → Adafruit Breakout GND |
| AtomS3 Lite Grove Red | 5 V | Grove / STEMMA QT変換 → Adafruit Breakout VIN |
| AtomS3 Lite Grove White | GPIO1 / SDA | Grove / STEMMA QT変換 → Adafruit Breakout SDA |
| AtomS3 Lite Grove Yellow | GPIO2 / SCL | Grove / STEMMA QT変換 → Adafruit Breakout SCL |
| Adafruit SEN6x Breakout JST GH | 6-pin sensor cable | SEN66 |

I2Cアドレスは`0x6B`、周波数は100 kHzです。GPIOはAtomS3 Lite用PlatformIO環境で明示的に設定し、
[`src/app_config.h`](../src/app_config.h)から一元的に使用します。`Wire.begin()`のデフォルトピンには依存しません。

Grove → STEMMA QT変換ケーブルについて、White/GPIO1がSDA、Yellow/GPIO2がSCLへ接続されることを、使用する製品の配線資料または導通確認で必ず確認してください。ケーブルの実配線が異なる場合は、`platformio.ini`のAtomS3 Lite環境にある`OMK_I2C_SDA_PIN`と`OMK_I2C_SCL_PIN`を交換します。

起動時の`SEN66 detected at 0x6B`ログは、全アドレスを走査せずに対象アドレスへI2C疎通確認した結果です。検出できない場合は、SDA/SCLの対応、GND、Breakoutの給電、SEN66のJST GHケーブル接続を確認してください。
