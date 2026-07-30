# OMK SEN66 node

ESP32 DevKitCとSEN66を接続し、1秒ごとの測定値をUSBシリアルのJSON Linesと、Raspberry Pi上のMosquittoへ送信するPlatformIOプロジェクトです。SEN66の計測とシリアル出力はWi-Fi/MQTTの接続状態に関係なく継続します。

## ハードウェア

- ボード: ESP32 DevKitC相当（PlatformIO board ID: `esp32dev`）
- センサ: Sensirion SEN66（SDA GPIO 21、SCL GPIO 22、I2Cアドレス`0x6B`、100 kHz）

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

詳細は[リポジトリのMQTT仕様](../../../docs/mqtt.md)を参照してください。現在の匿名・平文MQTTはOMK専用LANでの初期確認用です。運用前に認証、ACL、TLSを導入する予定です。

このノードは取得とMQTT publishだけを担当します。Raspberry Pi上の汎用`sensor-collector`が`omk/#`を購読してJSONLへ一次保存するため、SEN66固有の保存処理やクラウド接続はこのファームウェアへ追加しません。

## ビルド、書き込み、確認

```bash
cd firmware/esp32/sen66-node
pio run
pio run --target upload
pio device monitor --baud 115200
```

ポートを明示する場合は`--upload-port`または`--port`を追加します。起動後はSEN66初期化ログ、Wi-FiのIP/RSSI、MQTT接続ログ、JSON Linesを確認します。測定publishの成功ログは毎秒出力せず、失敗時だけ警告します。

Mosquitto側はリポジトリのルートで起動できます。

```bash
docker compose up -d mosquitto
docker compose logs --tail=100 mosquitto
```

ホスト上での受信確認には、`mosquitto_sub -h 192.168.50.1 -p 1883 -t 'omk/#' -v`を使います（クライアントが既にある場合）。ComposeはポートをAP側の`192.168.50.1:1883`だけにbindするため、`127.0.0.1`では接続できない場合があります。

Wi-Fi断、Broker停止、ESP32再起動後も、接続が戻れば自動復旧します。通信断中もSEN66測定とシリアル出力は継続することを実機で確認してください。

## ライブラリ

- [Sensirion I2C SEN66 1.3.1](https://github.com/Sensirion/arduino-i2c-sen66)
- [Sensirion Core 0.7.3](https://github.com/Sensirion/arduino-core)
- [PubSubClient 2.8](https://github.com/knolleary/pubsubclient)
