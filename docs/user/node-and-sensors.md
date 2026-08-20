# Nodeとセンサ

OMKの共通ESP32 Nodeは、SEN66などのI2CセンサをGatewayへ送るNode、BLEセンサをGatewayへ中継するrelay、ESP-WIFI-MESHの中継Nodeを兼ねます。SEN66を接続していないNodeもBLE relayとして動作でき、SEN66、BLE relay、Mesh/MQTTは同時に動作します。production対象はAtomS3 Liteです。

## 初回設定

Nodeの初回登録とWi-Fi設定はUSB Serial/JTAG Provisioningを使います。SSID、PSK、Node IDを手入力したり、物理ボタンを押したりする方式は前提にしません。Gatewayの手順と接続先は[共通Node README](../../firmware/esp32/omk-node/README.md)を参照してください。

credential分離版へ更新した既存NodeでWi-Fi/Meshが始まらない場合は、GatewayへUSB接続して`python3 scripts/provision_omk_node_via_usb.py --device /dev/ttyACM0 --profile omk-ap`を実行し、専用Gateway credential storeへ再Provisioningします。詳細なmigration条件は[ESP-WIFI-MESH Node networking decision](../decisions/esp-wifi-mesh-node-networking.md)を参照してください。

## BLEセンサ

Gateway direct BLEが通常の受信経路です。Node relayは到達範囲を補うための経路で、Gateway directが約30秒途絶した場合に利用され、directの復帰時はそちらへ戻ります。NodeはBLE advertisementをMQTTで中継し、Gateway側で登録済みセンサへ対応付けます。

BLEセンサの探索・登録はDashboardの管理メニューから行います。詳細は[Dashboard](dashboard.md)を参照してください。

## Meshによる通信範囲拡張

保存済みGateway Wi-Fi credentialを持つNodeはESP-WIFI-MESHを自動形成します。root、parent、child、Node別SSID、IPを手動設定する必要はありません。配置・電波条件に応じてNode自身がrootまたは中継を選び、parent/rootの喪失後は自動再構成します。

rootはGateway APへ接続し、childはroot経由で通常のMQTT通信を行います。GatewayへMesh daemonを追加する必要はなく、市販Wi-Fi中継機も必須ではありません。ただし、通信範囲は住宅の壁、階層、Nodeの設置位置、電波条件に依存します。NodeはAC電源で常時稼働させる前提です。

Mesh診断は`omk/node/<node_id>/status`へ30秒ごとにpublishされます。起動直後や再構成中は一時的なlayer、parent、IPやdisconnect counterの増加があり得るため、Dashboardでは安定化時間を考慮します。

## 筐体

AtomS3 LiteとSEN66を主な対象とする試作筐体のSTLが公開されています。ファイル一覧と使用上の注意は[sen66-node筐体README](../../hardware/enclosures/sen66-node/README.md)を参照してください。

旧SEN66専用firmwareは既存機器向けに残っています。新規開発は原則として共通Nodeを使用してください。
