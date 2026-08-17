# USB Serial/JTAG Provisioningを正式採用

## 決定

Node初回登録の正式方式はUSB Serial/JTAG Provisioningとする。初回だけNodeをGatewayへUSB接続し、Gateway CLIがUSB serial候補を列挙してversioned `identify` JSON応答からNode IDを取得する。GatewayのOMK AP SSID/PSKを送信すると、Nodeは既存Wi-Fi credential storageへ保存してread-back照合し、software reboot後に通常のWi-Fi STA/MQTT registrationを開始する。AtomS3 LiteでMQTT registration statusまでのE2Eを確認済みである。

SSID、PSK、Node ID、物理ボタンの入力は不要である。USB抜き差しをreset手段として前提にせず、共通firmwareへcredentialだけを後から追加設定する。transport、credential storage、network backendは分離しているため、将来のESP-Mesh-Lite移行でもUSB transportを維持できる。

## 不採用方式

- BLE GATT provisioning: Raspberry PiからAtomS3 Liteへの接続が不安定で、`le-connection-abort-by-local`、HCI `0x3e`が発生した。PPCP/MTU/PHY等の低レイヤ調査は停止し、保守コストを許容しない。
- BLE Advertisement config transport: Raspberry Pi kernel 6.18とBlueZ 5.82でLE Advertisement登録が`Invalid Parameters (0x0d)`となり、Gateway依存が強い。
- Temporary SoftAP + HTTP: Node側のWPA2 SoftAP、`/health`、`/config`、Node固有setup PSK、credential保存はPoC済み。ただし単一`wlan0`をOMK AP→STA→Node SoftAP→OMK APと切替えるGateway経路が不安定だった。
- ESP-NOW / Wi-Fi Aware等: Raspberry Pi側の特殊実装・driver依存・長期保守負担がUSB方式より大きい。

USB方式はLinux BLE stackやGateway Wi-Fi構成に依存せず、通常firmwareと通常のBLE sensor relayを維持できる。
