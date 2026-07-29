# OMK SEN66 node

ESP32 DevKitCにI2C接続したSensirion SEN66から、PM1.0、PM2.5、PM4.0、PM10、相対湿度、温度、VOC Index、NOx Index、CO2を連続取得し、USBシリアルへJSON Linesで出力するPlatformIOプロジェクトです。

Wi-Fi、MQTT、HTTP、外部保存、OTA、Bluetooth、およびセンサ値による制御はこの段階では実装していません。

## ハードウェア

- 対象ボード: ESP32 DevKitC相当（PlatformIO board ID: `esp32dev`）
- センサ: Sensirion SEN66
- I2C: SDA GPIO 21、SCL GPIO 22、アドレス `0x6B`、100 kHz

詳細な配線は[docs/wiring.md](docs/wiring.md)を参照してください。SEN66は3.3 Vで給電します。

## 開発環境とビルド

VS CodeのPlatformIO拡張、またはPlatformIO Coreを使用します。プロジェクトのルートはこの`sen66-node`ディレクトリです。

```bash
cd firmware/esp32/sen66-node
pio run
```

`platformio.ini`はESP32 Arduino framework、Sensirion I2C SEN66 `1.3.1`、Sensirion Core `0.7.3`を固定しています。初回のビルド時にPlatformIOが依存ライブラリを取得します。

## 書き込みとシリアルモニタ

USB接続したESP32へ書き込むには、同じディレクトリで実行します。

```bash
pio run --target upload
pio device monitor
```

ポートを明示する場合:

```bash
pio run --target upload --upload-port COM3
pio device monitor --port COM3 --baud 115200
```

Windowsでは、USBを接続したWindows側のVS Code + PlatformIOから書き込む方法が最も簡単です。WSL2から書き込む場合は、対象USBシリアルデバイスをWSL2へ明示的に共有し、`--upload-port /dev/ttyUSB0`（環境により異なる）を指定してください。

## 出力

起動時は人が読めるログを出力し、通常の測定値は1行につき1レコードのJSON Linesです。

```text
[INFO] OMK SEN66 node starting
[INFO] Firmware version: 0.1.0
[INFO] I2C initialized: SDA=21 SCL=22
[INFO] SEN66 detected at 0x6B
[INFO] SEN66 serial number: xxxxxxxxx
[INFO] Continuous measurement started
{"type":"measurement","uptime_ms":123456,"pm1_0_ug_m3":2.10,"pm2_5_ug_m3":3.40,"pm4_0_ug_m3":4.00,"pm10_0_ug_m3":4.80,"relative_humidity_percent":48.20,"temperature_celsius":25.60,"voc_index":102.00,"nox_index":null,"co2_ppm":612.00}
```

SEN66が示す未取得値（起動直後のNOx/CO2を含む）は0に置き換えず、JSONの`null`として出力します。測定データ未準備は異常ではなく、次の1秒周期で再試行します。

## エラー時の確認

| ログ | 確認事項 |
| --- | --- |
| `SEN66 initialization failed: SEN66 not found...` | 3.3 V/GND、SDA/SCLの配線とアドレス`0x6B`を確認する。 |
| `Continuous measurement start failed` | SEN66の給電、I2C配線、シリアルに表示されたエラーコードを確認する。 |
| `Measurement data not ready` | 起動直後には起こり得る正常な状態。待機する。 |
| `SEN66 read failed` | 一時的なI2Cエラー。3回連続で失敗すると測定停止、再初期化、測定再開を試みる。 |

センサ未接続時は5秒ごとに初期化を再試行します。読み取り失敗だけでESP32を自動再起動しません。

## ライブラリとライセンス

- [Sensirion I2C SEN66 1.3.1](https://github.com/Sensirion/arduino-i2c-sen66)（SensirionのBSD 3-Clause License）
- [Sensirion Core 0.7.3](https://github.com/Sensirion/arduino-core)（SensirionのBSD 3-Clause License）

SEN66とのI2Cコマンド、CRC、変換は公式ライブラリを使用し、プロジェクト側では再実装していません。

## 今後

SEN66処理は`Sen66Sensor`と`Sen66Measurement`に分離しています。次段階では、この構造体をWi-Fi/MQTT送信モジュールへ渡す形で、OMKゲートウェイとの通信を追加します。
