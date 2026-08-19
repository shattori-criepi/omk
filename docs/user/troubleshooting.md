# トラブルシューティング

まずGatewayへSSH接続し、対象サービスの状態を確認します。詳細な開発デバッグは各サービスREADMEを参照してください。

| 症状 | 最初の確認 |
|---|---|
| Gatewayへ接続できない | 電源、LAN/Wi-Fi、SSH接続先、Raspberry Piの起動状態 |
| Dashboardが開かない | `docker compose ps`、`curl http://localhost:8000/health`、Dashboardログ |
| MQTTデータが来ない | `docker compose logs --tail 100 mosquitto sensor-collector`、NodeのOMK AP接続 |
| ESP32 Nodeが見えない | USB接続、NodeのProvisioning状態、[共通Node README](../../firmware/esp32/omk-node/README.md) |
| BLEセンサが見えない | Dashboardのセンサ管理、Bluetooth adapter、BLE managerの状態 |
| Bルートを取得できない | DashboardのBルート状態、USBアダプタ、`omk-broute-meter.service` |
| APへ接続できない | `nmcli connection show omk-ap`、`nmcli device status` |
| AP端末がInternetへ出られない | 正常な仕様です。Gatewayローカル、MQTT、Dashboardへの接続を確認します |
| reboot後に戻らない | `systemctl status omk-system-manager.service omk-data-transformer.timer`、`docker compose ps` |

Gateway、AP、kioskの詳細な確認コマンドは[Gatewayセットアップ](gateway-setup.md)にあります。
