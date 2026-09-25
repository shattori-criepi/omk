# ハードウェア成果物

このディレクトリには、現行構成で再現可能な筐体などのハードウェア成果物を置いています。Gateway、Docker、NetworkManagerの設定手順は[Gatewayセットアップ](../docs/user/gateway-setup.md)、内部のネットワーク設計は[ネットワーク設計](../docs/developer/networking.md)を参照してください。

## 現在の構成

- Gateway: Raspberry Pi 4、64-bit Raspberry Pi OS
- Node: AtomS3 LiteのOMK Node。SEN66等のI2CセンサとBLE relayを扱う
- Bルート: 対応USBシリアルアダプタをGatewayのhost serviceで利用
- BLE: Gateway direct BLEを主系、Node relayを補助経路として利用
- 住宅用PV・蓄電池・PCS: 現行の単一検証profile向けECHONET Lite連携はGatewayとは別Raspberry Piで動作

## ディレクトリ

- [`enclosures/sen66-node/`](enclosures/sen66-node/README.md): SEN66 Node試作筐体の格納済みSTLと使用上の注意
- [`enclosures/omk-gateway/`](enclosures/omk-gateway/README.md): SORACOM OnyxをGatewayへ取り付けるための固定用3Dプリント部品

SEN66計測にもBLE中継にも、AtomS3 Liteの共通Node firmwareを使用します。
