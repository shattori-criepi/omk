# sensor-collector

Mosquittoの`omk/#`を購読し、payloadの機種別仕様を解釈せず日次JSONLへ追記するサービスです。`status`トピックも収集します。

各取得処理と保存処理を分離するための一次保存サービスです。MQTTトピックは`omk/<device_id>/<data_type>`を基本とし、新しいセンサや測定項目の追加にcollectorの変更は必要ありません。CSV変換、分析、可視化、外部送信はJSONL保存後の責務です。

## 設定

| 環境変数 | デフォルト | 説明 |
| --- | --- | --- |
| `MQTT_HOST` | `mosquitto` | MQTT brokerのComposeサービス名 |
| `MQTT_PORT` | `1883` | MQTT brokerポート |
| `MQTT_TOPIC` | `omk/#` | 購読トピック |
| `DATA_ROOT` | `/app/data/sensors` | JSONL保存先 |
| `TZ` | `Asia/Tokyo` | コンテナのタイムゾーン |
| `LOG_LEVEL` | `INFO` | ログレベル |

各行には、Raspberry Pi側で付与した`received_at`、`topic`、`qos`、`retain`、および受信payloadを入れます。通常のJSON payloadは`payload`にそのままネストします。不正JSONは`payload_raw`と解析エラーを、UTF-8でないデータはBase64を保存します。

## 実行と確認

```bash
docker compose up -d --build sensor-collector
docker compose logs --tail=100 sensor-collector
tail -n 1 data/sensors/$(date +%Y)/$(date +%m)/$(date +%d).jsonl | python3 -m json.tool
```

MQTTの初回接続、broker再起動後の再接続には、Paho MQTTの上限60秒の指数バックオフを使用します。collectorを再起動しても既存の日次ファイルへ追記します。
