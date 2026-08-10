# SEN66 と M5Stack AtomS3 Lite の配線

AtomS3 LiteとSEN66本体を直接配線しません。Adafruit SEN6x Breakoutを必ず介在させます。AtomS3 Lite Groveポートの5 VはBreakoutのVINへ入り、Breakout搭載のレギュレータとI2Cレベルシフタを通じてSEN66へ接続されます。SEN66本体へ5 Vを直接入力してはいけません。

| 接続区間 | 信号 | 接続先 |
| --- | --- | --- |
| AtomS3 Lite Grove Black | GND | Grove / STEMMA QT変換 → Adafruit Breakout GND |
| AtomS3 Lite Grove Red | 5 V | Grove / STEMMA QT変換 → Adafruit Breakout VIN |
| AtomS3 Lite Grove White | GPIO1 / SCL | Grove / STEMMA QT変換 → Adafruit Breakout SCL |
| AtomS3 Lite Grove Yellow | GPIO2 / SDA | Grove / STEMMA QT変換 → Adafruit Breakout SDA |
| Adafruit SEN6x Breakout JST GH | 6-pin sensor cable | SEN66 |

I2Cアドレスは`0x6B`、周波数は100 kHzです。GPIOはAtomS3 Lite用PlatformIO環境で明示的に設定し、
[`src/app_config.h`](../src/app_config.h)から一元的に使用します。`Wire.begin()`のデフォルトピンには依存しません。

Grove → STEMMA QT変換ケーブルを使用した実機で、SDA=GPIO2（Yellow）、SCL=GPIO1（White）によりSEN66の測定値取得を確認しました。この対応をAtomS3 Liteの正式な配線仕様とします。`esp32dev`および`omk-esp32-c3`環境のGPIO設定には影響しません。

起動時の`SEN66 detected at 0x6B`ログは、全アドレスを走査せずに対象アドレスへI2C疎通確認した結果です。検出できない場合は、SDA/SCLの対応、GND、Breakoutの給電、SEN66のJST GHケーブル接続を確認してください。

実機ではAtomS3 LiteへのUSB書き込み、USB Serial monitor、SEN66 I2C通信、およびPM1.0、PM2.5、PM4.0、PM10、相対湿度、温度、VOC Index、NOx Index、CO2の測定値取得を確認済みです。Wi-Fi接続、MQTT Brokerへの継続publish、OMK Gatewayでの受信、およびOMK Dashboardでの測定値表示も確認済みです。
