# Nodeとセンサ

OMKの共通ESP32 Nodeは、SEN66などのI2CセンサをGatewayへ送るNodeと、BLEセンサをGatewayへ中継するrelayの両方に使えます。SEN66を接続していないNodeもBLE relayとして動作できます。

## 初回設定

Nodeの初回登録とWi-Fi設定はUSB Serial/JTAG Provisioningを使います。SSID、PSK、Node IDを手入力したり、物理ボタンを押したりする方式は前提にしません。Gatewayの手順と接続先は[共通Node README](../../firmware/esp32/omk-node/README.md)を参照してください。

## BLEセンサ

Gateway direct BLEが通常の受信経路です。Node relayは到達範囲を補うための経路で、Gateway directが約30秒途絶した場合に利用され、directの復帰時はそちらへ戻ります。NodeはBLE advertisementをMQTTで中継し、Gateway側で登録済みセンサへ対応付けます。

BLEセンサの探索・登録はDashboardの管理メニューから行います。詳細は[Dashboard](dashboard.md)を参照してください。

## 今後の通信範囲拡張

Node間中継でWi-Fi通信範囲を広げる方針がありますが、具体的な方式は未確定です。ESP-MESHなど特定技術を前提にした設定は行わないでください。

## 筐体

AtomS3 LiteとSEN66を主な対象とする試作筐体の成果物は[sen66-node筐体README](../../hardware/enclosures/sen66-node/README.md)に置きます。

旧SEN66専用firmwareは既存機器向けに残っています。新規開発は原則として共通Nodeを使用してください。
