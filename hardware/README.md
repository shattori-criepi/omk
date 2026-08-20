# ハードウェア成果物

このディレクトリは筐体など、現行構成で再現可能なハードウェア成果物の正本です。Gateway、Docker、NetworkManagerの設定手順は[Gatewayセットアップ](../docs/user/gateway-setup.md)、内部のネットワーク設計は[ネットワーク設計](../docs/developer/networking.md)を参照してください。

## 現在の構成

- Gateway: Raspberry Pi 4または5、64-bit Raspberry Pi OS
- Node: 共通ESP32 Node。SEN66等のI2CセンサとBLE relayを扱う
- Bルート: 対応USBシリアルアダプタをGatewayのhost serviceで利用
- BLE: Gateway direct BLEを主系、Node relayを補助経路として利用
- パワコン: 現行の一条設備向け連携はGatewayとは別Raspberry Piで動作

## ディレクトリ

- [`enclosures/sen66-node/`](enclosures/sen66-node/README.md): SEN66 Node試作筐体の格納済みSTLと使用上の注意

ESP32-C3＋SEN66一体型PCB Rev.Aの設計資産は中止に伴い削除しました。再試行時に役立つ知見は[設計判断](../docs/decisions/esp32-c3-integrated-pcb-cancellation.md)に残しています。既存SEN66専用Nodeは維持しますが、新規開発は共通Nodeを基本とします。
