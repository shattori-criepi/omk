# ichijo-energy-node

一条工務店の太陽光発電・蓄電池システムがローカルLANで公開するECHONET Lite値を、OMKのMQTTブローカーへ送る読み取り専用サービスです。標準ライブラリの小さなUDP実装を使い、GET以外のECHONET Lite要求、機器設定変更、探索、ブロードキャスト、ルーティングは行いません。

## 位置づけ

`ichijo-energy-node`は、OMKの標準構成や必須サービスには含まれないオプションサービスです。一条工務店の特定の太陽光発電・蓄電池システムを対象にしています。

現時点では開発者宅の1住宅・1実機構成でのみ動作確認しています。すべての一条工務店住宅、パワーコンディショナ、蓄電池構成での動作を保証するものではありません。対象設備がないOMK環境では、導入、設定、起動する必要はありません。

既存のBルート、SEN66、sensor-collectorとは独立したサービスであり、未使用時にOMKの標準機能へ影響しません。同種の住宅設備やパワーコンディショナのローカルデータをMQTTへ変換するエッジノードを実装する際の参考実装として、OMKリポジトリ内に保持します。

一条設備固有のECHONET Liteオブジェクト、プロパティ、単位、符号規則、状態コードを前提とするため、現時点では汎用的な`power-conditioner-node`などへ改名しません。他設備へ流用する場合は、対象実機でEOJ、EPC、単位、符号、状態コードをあらためて確認してください。

```text
Ichijo ECHONET node -- UDP/3610, GET only --> Raspberry Pi --> MQTT --> OMK collector (omk/#)
```

`sensor-collector`はすでに`omk/#`を購読しているため、`power-flow`と`status`は変更なしでJSONL一次保存されます。

## 対象と変換

| 値 | EOJ / EPC | EDT | 実機確認済みの規則 |
| --- | --- | --- | --- |
| PV瞬時発電 | `027901` / `E0` | unsigned 2 bytes | W、正値 |
| 蓄電池SOC | `027D01` / `E4` | unsigned 1 byte | % |
| 蓄電池充放電 | `027D01` / `D3` | signed 4 bytes | 正=充電、負=放電 |
| 蓄電池運転状態 | `027D01` / `CF` | unsigned 1 byte | `42` charging、`43` discharging、`44` standby |
| 系統瞬時電力 | `028701` / `C6` | signed 4 bytes | 正=買電、負=売電 |
| PCS交流瞬時電力 | `02A501` / `E7` | signed 4 bytes | 実機で負=交流側出力 |

未知の状態コードは`unknown`とし、`battery_operating_state_raw`にも生コードを残します。PCS E7は現時点の実機確認範囲に基づき`pcs_ac_output_power_w = max(-raw, 0)`とします。正値の意味（系統から蓄電池への充電など）は未確認です。

電力収支は`PV + 買電 + 放電 - 売電 - 充電`です。必須値が欠けた場合は`load_power_w`を`null`、`quality`を`degraded`にします。負の収支値は0に丸めず保持し、同じく`degraded`とします。`measured_at`は全プロパティを読み終えた取得サイクル終了時刻で、JSTのISO 8601です。

## 設定と起動

`cp .env.example .env`の後、実機値に合わせて環境変数を設定します（`.env`はGit管理外）。主な値は`ICHJO_DEVICE_ID`（既定`ichijo-001`）、`ICHJO_ECHONET_TARGET_IP`（`192.168.8.182`）、`ICHJO_ECHONET_INTERFACE`（`eth0`）、`ICHJO_POLL_INTERVAL_SECONDS`（`10`）、`ICHJO_ECHONET_TIMEOUT_SECONDS`（`2`）、`ICHJO_ECHONET_RETRY_COUNT`（`1`）、`ICHJO_PROPERTY_INTERVAL_SECONDS`（`0.05`）、`MQTT_HOST`、`MQTT_PORT`（`1883`）、`MQTT_KEEPALIVE_SECONDS`（`60`）です。ローカルIPは指定インターフェースから取得し、UDP送受信とも3610を使います。

```bash
cd services/ichijo-energy-node
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=src .venv/bin/python -m ichijo_energy_node

# 実機導入時: MQTTへ送らず1サイクルを標準出力
PYTHONPATH=src .venv/bin/python -m ichijo_energy_node --once --no-mqtt

# 実機ネットワークなしの単体テスト
PYTHONPATH=src .venv/bin/python -m pytest
```

## MQTT

QoSは0です。`omk/<device_id>/power-flow`はサイクルごとに1件、retainなしで送ります。`omk/<device_id>/status`はretainありで、接続時`online`、正常終了時`offline`を送信し、Last Willにも`offline`を設定します。Paho MQTTの1～30秒指数バックオフを使用します。

```json
{"device_id":"ichijo-001","measured_at":"2026-08-03T15:32:14+09:00","pv_power_w":410,"battery_soc_percent":48,"battery_charge_power_w":0,"battery_discharge_power_w":683,"battery_operating_state":"discharging","battery_operating_state_raw":67,"grid_import_power_w":14,"grid_export_power_w":0,"pcs_ac_output_power_w":1093,"load_power_w":1107,"quality":"normal","errors":[]}
```

## トラブルシューティングと未確認事項

`Network configuration error`は対象インターフェースにIPv4がない状態です。`ECHONET timeout`は対象IP、同一L2接続、UDP 3610、送信元が3610であることを確認します。DEBUGログで生フレームを確認できますが、通常ログへは出しません。

対象IPの将来的な変更、自動探索、E7正値の意味、長期連続運転、ネットワーク切断からの実機復旧、OMK専用AP経由のMQTT、systemd自動起動、data-transformer対応、dashboard対応は未実装・未確認です。
