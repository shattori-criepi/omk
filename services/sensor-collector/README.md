# sensor-collector

Mosquittoの`omk/#`を購読し、payloadの機種別仕様を解釈せず日次JSONLへ追記するサービスです。`status`トピックも収集します。

各取得処理と保存処理を分離するための一次保存サービスです。MQTTトピックは`omk/<device_id>/<data_type>`を基本とし、新しいセンサや測定項目の追加にcollectorの変更は必要ありません。CSV変換、分析、可視化、外部送信は別コンポーネントで行います。Harvest uploaderはMQTTを独立して購読し、JSONL保存の完了を待ちません。

## 設定

| 環境変数 | デフォルト | 説明 |
| --- | --- | --- |
| `MQTT_HOST` | `mosquitto` | MQTT brokerのComposeサービス名 |
| `MQTT_PORT` | `1883` | MQTT brokerポート |
| `MQTT_TOPIC` | `omk/#` | 購読トピック |
| `DATA_ROOT` | `/app/data/sensors` | JSONL保存先 |
| `LATEST_DATA_ROOT` | `/app/data/latest` | dashboard向け最新状態JSONの保存先 |
| `TZ` | `Asia/Tokyo` | コンテナのタイムゾーン |
| `LOG_LEVEL` | `INFO` | ログレベル |

各行には、Raspberry Pi側で付与した`received_at`、`topic`、`qos`、`retain`、および受信payloadを入れます。通常のJSON payloadは`payload`にそのままネストします。不正JSONは`payload_raw`と解析エラーを、UTF-8でないデータはBase64を保存します。

## 汎用latest store（Dashboard可変化の基盤）

JSONLへの保存成功後、正常なJSON payloadのうち、`omk/<topic-device-id>/<data-type>`かつ payloadに文字列`device_id`を持つレコードから、表示候補になり得るscalar値を`LATEST_DATA_ROOT/items/<stable-item-id>.json`へ保存します。同時に`LATEST_DATA_ROOT/catalog.json`を更新するため、Dashboardは全候補をディレクトリ走査なしで列挙できます。Dashboardはこのstoreを表示候補と表示設定の基準として使用します。

stable item IDは`SHA-256(topic + NUL + device_id + NUL + field)`のhexに`item_v1_`を付けたものです。したがって同じ`power` topic typeや`temperature_c` fieldでも、topicまたはdevice_idが異なれば衝突しません。itemファイルには最新のraw scalar値、型、`measured_at`（payloadにある場合）、`received_at`、MQTT QoS/retainを、catalogにはID、source、型、最終受信時刻を保存します。catalogは検出済み候補を自動削除しないため、受信停止や collector再起動後も候補は残ります。

候補は有限のnumber、boolean、64文字以下の制御文字を含まないstringです。`device_id`、`*_at`、`*_id`、`*_raw`、quality、errors、source、firmware、uptime等の時刻・識別子・診断メタデータは除外します。表示名、単位、精度、カテゴリ、意味上の電力スロットはDashboardで解釈します。

各JSONは同一ディレクトリの一時ファイルからatomic replaceで更新し、`0644`で公開します。10秒程度の更新頻度では、対象scalarごとのitem置換と1回の小さなcatalog置換だけを行います。

### 既存Dashboardとの互換性

互換性のため固定latestも併行して維持します。Bルート`power`で`net_power_w`を持つものは`broute_power.json`、`sen66`は`sen66.json`、`power-flow`は`ichijo_power_flow.json`へ従来どおり保存します。BLE Plugの`power_w`/`switch_state`はgeneric storeには保存されますが、`broute_power.json`を上書きしません。

不正JSON、Base64 payload、topic形式または`device_id`が不正なpayloadはgeneric latestを更新しません。最新状態の保存に失敗しても、JSONL収集は継続します。

latest JSONは置換後に`0644`へ設定するため、ホストユーザーおよび読み取り専用でマウントしたdashboardから読み取れます。

## 実行と確認

```bash
docker compose up -d --build sensor-collector
docker compose logs --tail=100 sensor-collector
tail -n 1 data/sensors/$(date +%Y)/$(date +%m)/$(date +%d).jsonl | python3 -m json.tool
```

MQTTの初回接続、broker再起動後の再接続には、Paho MQTTの上限60秒の指数バックオフを使用します。collectorを再起動しても既存の日次ファイルへ追記します。
