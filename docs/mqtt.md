# MQTT仕様

OMKのESP32センサノードは、OMK専用Wi-Fi経由でRaspberry Pi上のMosquittoへ測定値を送る。ESP32がクラウドへ直接接続することはない。Brokerは初期構成では`192.168.50.1:1883`で待ち受け、Composeのポート公開もこのAP側IPに限定する。

## トピックとdevice_id

`device_id`は各ノードを識別する設定値である。現時点では`network_config.h`の固定値を使い、将来MACアドレス由来のIDへ変更できるよう、トピックとpayloadで直接固定しない。

| 用途 | トピック | QoS | retain |
| --- | --- | --- | --- |
| SEN66測定値 | `omk/<device_id>/sen66` | 0 | false |
| 接続状態 | `omk/<device_id>/status` | 0 | true |

初期ノードの例は`omk/sen66-001/sen66`と`omk/sen66-001/status`である。

## Payload

測定値はSEN66の取得成功時に送信する。未取得値は非標準の`NaN`ではなくJSONの`null`にする。ESP32はまだ時刻同期をしていないため`measured_at`を含めず、受信時刻の付与またはNTP導入は後続課題とする。

```json
{"device_id":"sen66-001","uptime_ms":123456,"pm1_0_ug_m3":4.1,"pm2_5_ug_m3":6.8,"pm4_0_ug_m3":8.2,"pm10_0_ug_m3":10.5,"relative_humidity_percent":48.2,"temperature_celsius":25.3,"voc_index":92.0,"nox_index":null,"co2_ppm":612.0}
```

MQTT接続直後はretain付きで次を送信する。

```json
{"device_id":"sen66-001","status":"online","firmware_name":"omk-sen66-node","firmware_version":"0.1.0"}
```

接続時にはLast Willを設定し、予期しない切断ではretain付きで次を送信する。

```json
{"device_id":"sen66-001","status":"offline"}
```

## セキュリティと運用

現在のMosquitto設定はOMK専用LANでの初期確認用であり、匿名・平文接続である。将来はユーザー名・パスワード、ACL、TLSを追加する。MQTTポートをSORACOM側または他のホストインターフェースに公開してはならない。
