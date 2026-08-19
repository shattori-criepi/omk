# OMK SEN66 node

> **既存SEN66専用系統**: このfirmwareは既存機器向けに維持します。新規開発は、SEN66計測とBLE relayを扱える[共通OMK Node](../omk-node/README.md)を基本としてください。以下の固定SSID・device ID例はこの既存系統の例であり、新規Gatewayの推奨設定ではありません。

M5Stack AtomS3 LiteとSEN66を接続し、10秒ごとの測定値をUSBシリアルのJSON Linesと、Raspberry Pi上のMosquittoへ送信するPlatformIOプロジェクトです。SEN66の計測とシリアル出力はWi-Fi/MQTTの接続状態に関係なく継続します。

## ハードウェア

- ボード: M5Stack AtomS3 Lite（ESP32-S3、PlatformIO board ID: `m5stack-atoms3`）
- センサ: Sensirion SEN66（I2Cアドレス`0x6B`、100 kHz）
- 接続: AtomS3 Lite Grove（HY2.0-4P）→ Grove / STEMMA QT変換 → Adafruit SEN6x Breakout → JST GH 6-pin cable → SEN66

AtomS3 LiteのGroveポートでは、実機確認済みの配線としてYellow / GPIO2をSDA、White / GPIO1をSCLとして明示的に初期化します。
I2C速度はSEN66の上限に合わせて100 kHzです。USB-C経由の起動ログとシリアルモニタを有効にするため、
AtomS3 Lite環境には`ARDUINO_USB_CDC_ON_BOOT=1`を設定しています。
WSL2 + usbipd経由の書き込み安定化のため、AtomS3 Lite環境の`upload_speed`は115200 baudです。これはfirmware書き込み用の速度であり、SEN66のI2Cクロック（100 kHz）とは別の設定です。

従来の`esp32dev`（GPIO21/22）および`omk-esp32-c3`（GPIO6/7）環境は、既存ハードウェア用として残しています。

配線は[docs/wiring.md](docs/wiring.md)を参照してください。SEN66は3.3 Vで給電します。

## 接続設定

最初にローカル設定を作成します。`network_config.h`はGit管理対象外です。Wi-Fiパスワード、将来のMQTT認証情報、その他の秘密情報をコミットまたはシリアルログへ出力してはいけません。

```bash
cd firmware/esp32/sen66-node
cp src/network_config.example.h src/network_config.h
```

`src/network_config.h`でSSID、Wi-Fiパスワード、Broker、`DEVICE_ID`を設定します。初期環境のBrokerは`192.168.50.1:1883`、初期device IDは`sen66-001`です。ローカル作業ツリーには雛形が用意されていますが、書き込み前に必ずWi-Fiパスワードを実値へ置き換えてください。

## MQTT

PubSubClient `2.8`を使用します。Brokerへの接続と測定値の送信はWi-Fi接続後に行い、再接続はそれぞれ5秒間隔で試行します。再接続処理に待機ループは使いません。

| 用途 | トピック | QoS | retain |
| --- | --- | --- | --- |
| 測定値 | `omk/<device_id>/sen66` | 0 | false |
| 接続状態 | `omk/<device_id>/status` | 0 | true |

接続時には`online`状態をretain付きで送信し、Last Willにより予期しない切断時は`offline`をretain付きで送信します。測定値は次の形式です。SEN66が未取得の値は`null`で送信します。

```json
{"device_id":"sen66-001","uptime_ms":123456,"pm1_0_ug_m3":4.1,"pm2_5_ug_m3":6.8,"pm4_0_ug_m3":8.2,"pm10_0_ug_m3":10.5,"relative_humidity_percent":48.2,"temperature_celsius":25.3,"voc_index":92.0,"nox_index":null,"co2_ppm":612.0}
```

詳細は[リポジトリのMQTT仕様](../../../docs/developer/data-and-mqtt.md)を参照してください。現在の匿名・平文MQTTはOMK専用LANでの初期確認用です。運用前に認証、ACL、TLSを導入する予定です。

このノードは取得とMQTT publishだけを担当します。Raspberry Pi上の汎用`sensor-collector`が`omk/#`を購読してJSONLへ一次保存するため、SEN66固有の保存処理やクラウド接続はこのファームウェアへ追加しません。

## ビルド、書き込み、確認

```bash
cd firmware/esp32/sen66-node
pio run
pio run --target upload
pio device monitor --baud 115200
```

既定環境はAtomS3 Liteです。ポートを明示する場合は`--upload-port`または`--port`を追加します。
従来ボードをビルドする場合だけ、例えば`pio run -e esp32dev`または`pio run -e omk-esp32-c3`を指定します。

### WSL2 + usbipdでのUSB接続

AtomS3 Liteを抜き差しすると、WSL側のシリアルデバイスが消えることがあります。その場合はWindows側で対象デバイスを再attachします。

```powershell
usbipd list
usbipd attach --wsl --busid <BUSID>
```

WSL側では次で接続先を確認します。

```bash
ls -l /dev/ttyACM*
```

実機確認では`/dev/ttyACM0`を使用しましたが、BUSIDおよびtty番号は環境ごとに異なります。確認したデバイスを`--upload-port`または`--port`へ指定してください。

### AtomS3 Liteの実機確認手順

1. AtomS3 Lite Groveポートを、Grove / STEMMA QT変換ケーブルでAdafruit SEN6x Breakoutへ接続する。
2. SEN66をJST GH 6-pin cableでBreakoutへ接続する。
3. AtomS3 LiteをUSB-CでPCへ接続する。
4. `pio run`を実行する。
5. `pio run --target upload`を実行する。
6. `pio device monitor --baud 115200`を実行する。
7. 起動ログの`I2C initialized: SDA=2 SCL=1`および`SEN66 detected at 0x6B`を確認する。
8. `Continuous measurement started`と測定JSON Linesを確認する。
9. Wi-Fi接続ログとMQTT接続ログを確認する。
10. Gateway側で`mosquitto_sub -h 192.168.50.1 -p 1883 -t 'omk/#' -v`を実行し、測定topicを確認する。
11. Gatewayの`sensor-collector`がJSONLを保存したことを確認する。

Grove / STEMMA QT変換ケーブルを介した実機配線では、Yellow / GPIO2がSDA、White / GPIO1がSCLです。`0x6B`を検出できない場合は、GND、Breakoutの給電、SEN66のJST GHケーブル接続を確認してください。

起動後はSEN66初期化ログ、Wi-FiのIP/RSSI、MQTT接続ログ、JSON Linesを確認します。測定publishの成功ログは毎秒出力せず、失敗時だけ警告します。

Mosquitto側はリポジトリのルートで起動できます。

```bash
docker compose up -d mosquitto
docker compose logs --tail=100 mosquitto
```

ホスト上での受信確認には、`mosquitto_sub -h 192.168.50.1 -p 1883 -t 'omk/#' -v`を使います（クライアントが既にある場合）。ComposeはポートをAP側の`192.168.50.1:1883`だけにbindするため、`127.0.0.1`では接続できない場合があります。

Wi-Fi断、Broker停止、ESP32再起動後も、接続が戻れば自動復旧します。通信断中もSEN66測定とシリアル出力は継続することを実機で確認してください。

## AtomS3 Lite 実機確認結果

以下は確認済みです。

- AtomS3 LiteへのUSB書き込み
- USB Serial monitorでの起動ログ確認
- SEN66とのI2C通信（SDA=GPIO2、SCL=GPIO1、アドレス`0x6B`、100 kHz）
- SEN66測定値の取得（PM1.0、PM2.5、PM4.0、PM10、相対湿度、温度、VOC Index、NOx Index、CO2）
- Wi-Fi接続（SSID `OMK-1CA9A8`、IPアドレス `192.168.50.125`）
- Broker `192.168.50.1:1883`へのMQTT接続と測定値の継続publish
- OMK Gatewayでの測定値受信
- OMK DashboardでのSEN66測定値表示

`network_config.h`はこのプロジェクトとリポジトリルートの`.gitignore`で除外しています。実環境のSSID・パスワードはこのローカルファイルへだけ設定し、Git管理するのは`network_config.example.h`のみです。

## ライブラリ

- [Sensirion I2C SEN66 1.3.1](https://github.com/Sensirion/arduino-i2c-sen66)
- [Sensirion Core 0.7.3](https://github.com/Sensirion/arduino-core)
- [PubSubClient 2.8](https://github.com/knolleary/pubsubclient)
