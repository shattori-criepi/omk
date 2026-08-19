# ハードウェア成果物

このディレクトリはPCB、配線、筐体など再現可能なハードウェア成果物の正本です。Gateway、Docker、NetworkManagerの設定手順は[Gatewayセットアップ](../docs/user/gateway-setup.md)、内部のネットワーク設計は[ネットワーク設計](../docs/developer/networking.md)を参照してください。

## 現在の構成

- Gateway: Raspberry Pi 4または5、64-bit Raspberry Pi OS
- Node: 共通ESP32 Node。SEN66等のI2CセンサとBLE relayを扱う
- Bルート: 対応USBシリアルアダプタをGatewayのhost serviceで利用
- BLE: Gateway direct BLEを主系、Node relayを補助経路として利用
- パワコン: 現行の一条設備向け連携はGatewayとは別Raspberry Piで動作

## ディレクトリ

- `pcb/`: 回路図、PCB、部品・footprint・配置監査
- [`enclosures/sen66-node/`](enclosures/sen66-node/README.md): SEN66 Node試作筐体の格納済みSTLと使用上の注意
- `interconnect-review.md`など: 接続方式・部品選定の監査記録

PCB Rev.A中止の採否理由は[設計判断](../docs/decisions/esp32-c3-integrated-pcb-cancellation.md)を参照してください。既存SEN66専用Nodeは維持しますが、新規開発は共通Nodeを基本とします。
