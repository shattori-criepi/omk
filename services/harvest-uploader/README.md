# harvest-uploader

`harvest-uploader`はMQTTを独立クライアントとして`omk/#`購読し、受信時刻（Asia/Tokyo）の00秒から59秒までを住宅全体の1分レコードへ集約してSORACOM Harvest DataへPOSTします。JSONLは引き続き`sensor-collector`による10秒生データの一次保存であり、本サービスはJSONLやParquetを読まず変更もしません。

## MQTT入力

| トピック | 使用フィールド |
| --- | --- |
| `omk/<device_id>/sen66` | `temperature_celsius`、`relative_humidity_percent`、`co2_ppm`、`pm1_0_ug_m3`、`pm2_5_ug_m3`、`pm4_0_ug_m3`、`pm10_0_ug_m3`、`voc_index`、`nox_index` |
| `omk/<device_id>/power` | `net_power_w` |
| `omk/<device_id>/cumulative-energy` | `cumulative_energy_import_kwh`、`cumulative_energy_export_kwh` |
| `omk/<device_id>/power-flow` | `pv_power_w`、`load_power_w`、`grid_import_power_w`、`grid_export_power_w`、`pcs_ac_output_power_w`、`battery_soc_percent`、`battery_charge_power_w`、`battery_discharge_power_w` |
| `omk/<sensor_id>/environment` | `temperature_c`、`relative_humidity_percent`、`co2_ppm`（存在する項目のみ） |
| `omk/<sensor_id>/motion` | `motion_state` |
| `omk/<sensor_id>/contact` | `contact_state` |
| `omk/<sensor_id>/power` | `power_w`、`switch_state`（Plug Mini等の電力センサ） |

`power`の正値は買電、負値は売電です。電圧・電流・力率、蓄電池の運転状態はHarvestへ送信しません。不正JSON、JSON objectではないpayload、非有限数は安全に無視します。

### Thread model

Paho MQTTのnetwork callbackは、topic・payload・callback時点のJST受信時刻をthread-safeなFIFO inboxへ投入するだけです。集約、SQLite durable outbox、Harvest送信、retryは実行ループのmain threadだけがinboxをdrainした後に処理します。このため、処理遅延が分境界をまたいでも集約にはcallback時点の受信分を使い、SQLite connectionを複数threadで共有しません。

## Harvestフィールドと集約

SEN66は`sen66_temperature_c`、`sen66_relative_humidity_percent`、`sen66_co2_ppm`、`sen66_pm1_0_ug_m3`、`sen66_pm2_5_ug_m3`、`sen66_pm4_0_ug_m3`、`sen66_pm10_0_ug_m3`、`sen66_voc_index`、`sen66_nox_index`です。Bルートは`broute_grid_power_w`、`broute_grid_import_power_w`、`broute_grid_export_power_w`、`broute_grid_import_energy_kwh`、`broute_grid_export_energy_kwh`です。電力系は`power_system_pv_power_w`、`power_system_load_power_w`、`power_system_grid_import_power_w`、`power_system_grid_export_power_w`、`power_system_pcs_ac_output_power_w`、`power_system_battery_soc_percent`、`power_system_battery_charge_power_w`、`power_system_battery_discharge_power_w`です。

`time`は区間開始時刻（例: `2026-08-05T10:34:00+09:00`）です。温湿度・空気質・各瞬時電力は有効サンプルの平均、積算買売電量とSOCは区間末の最新値です。欠測フィールドはJSONから省略し、全項目欠測の区間は送信しません。開いた1分区間はメモリだけに保持するため、再起動時には意図的に破棄されます。

`environment` は汎用のセンサ入力です。許可された `sensor_id`（英数字、`-`、`_`）ごとに、`<sensor_id>_temperature_c`、`<sensor_id>_relative_humidity_percent`、`<sensor_id>_co2_ppm` を生成し、存在する各項目を1分平均で送信します。これらはSEN66の固定fieldとは独立して共存します。

`motion` は `<sensor_id>_motion_state` をその1分の最大値として送信します（0=その分に検知なし、1=一度以上検知）。`contact` は開閉回数を保存せず、状態変化が一度でもあれば `<sensor_id>_contact_changed` を1、最後に観測した状態を `<sensor_id>_contact_state` として送信します（0=閉、1=開）。分境界をまたぐ状態変化は次の分の変化として扱い、再起動後の最初の観測は変化に数えません。

電力センサの`power` payloadに`power_w`または`switch_state`がある場合は、許可された
`sensor_id`ごとに`<sensor_id>_power_w`（1分平均）と`<sensor_id>_switch_state`
（分内の最後の正常値、0=OFF、1=ON）を送信する。Bルートの既存`power` fieldとは独立して共存する。

SwitchBot Plug MiniのPi実機では、`plug-001_power_w`と`plug-001_switch_state`が
SORACOM Harvest Dataへ送信されることを確認した。分内で負荷を変えた例では瞬時値が約170 Wに達する一方、1分平均の`plug-001_power_w`は137.3 W、最後の状態
`plug-001_switch_state`は1となった。これは電力を平均、switch stateを最後の正常値として扱う仕様どおりである。

## 再送と設定

HTTP成功は2xxです。失敗したレコードは`HARVEST_QUEUE_PATH`のSQLite outboxに保持され、古い順に最大1件ずつ、最大60秒の指数バックオフで再送します。作成から`HARVEST_RETRY_MAX_AGE_SECONDS`（既定3600秒）を超えたものは警告ログとともに破棄します。SQLiteにより再起動後も再送待ちデータを保持します。

環境変数は`MQTT_HOST`（既定`mosquitto`）、`MQTT_PORT`（`1883`）、`MQTT_CLIENT_ID`（`omk-harvest-uploader`）、`MQTT_TOPIC`（`omk/#`）、`HARVEST_ENDPOINT`（`http://harvest.soracom.io`）、`HARVEST_TIMEOUT_SECONDS`（`10`）、`HARVEST_RETRY_MAX_AGE_SECONDS`（`3600`）、`HARVEST_QUEUE_PATH`、`TZ`です。認証情報・APIキーをpayloadやソースへ入れません。

Compose外で起動する場合や既定値を確認する場合は、secretを含まない[`.env.example`](.env.example)を参照してください。必要に応じてこれをローカルの`.env`へコピーし、接続先などを設定します。

Harvest Data側では、731日保持とカスタムタイムスタンプを利用できる設定を別途有効化してください。

## 起動・テスト

```bash
docker compose up -d --build harvest-uploader
docker compose logs -f harvest-uploader

cd services/harvest-uploader
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=src .venv/bin/python -m pytest
```

ローカルHTTPモックでは、別端末で`python3 -m http.server 8080`を起動し、`HARVEST_ENDPOINT=http://host.docker.internal:8080`を指定します（Dockerホストの到達先は環境に合わせて変更）。POST内容を検証するにはPOSTを記録する小さなHTTPハンドラを使ってください。実Harvestへの本番送信はこのリポジトリのテスト対象外です。
