# OMK ESP32 Node

ESP-IDF 6.0.1 / PlatformIO espressif32 7.0.1。AtomS3 Liteを主対象とする、native ESP-WIFI-MESH版です。実機導入前の実装であり、旧IP/NAT版の実機確認結果はこの版の安定性を保証しません。

## 通信

```text
各Node: SEN66 / BLE → bounded queue → esp_mesh_send(NULL)
                       ↓ SDKが多段中継・親選択・Root選出
Root: esp_mesh_recv → bounded queue → ESP-MQTT → Gateway内部topic
Gateway: SEN66の所有者/revision照合 → 従来の共通MQTT topic
Gateway: desired revision → MQTT config → Root → Mesh → 対象Node
```

非RootはIPアドレス、DHCP、TCP/MQTT接続を持ちません。Root候補として全Nodeに公式のSTA netifを一度だけ生成し、現在のRootだけがDHCP clientとMQTTを使用します。仮想netif、DHCP server、NAPT、netif再生成、独自parent選択、復旧用restart、MQTT patchは廃止しました。

- Mesh形成・親変更・Root選出はSDKのself-organized mode。固定Rootは設定しません。
- 上りは公式`esp_mesh_send(NULL, ...)`、受信は`esp_mesh_recv()`、下りは宛先MAC指定です。`recv_toDS()`によるIP/socket転送は使用しません。
- Mesh受信taskは有限queueへコピーするだけです。MQTT enqueueと設定のNVS保存は別taskで実行します。
- JSONは最大1024 bytes。周期値はQoS 0。有限queueと30秒の保持期限を持ち、満杯・期限切れ・Root交代では破棄します。SDK内の通過時間を含む端到端30秒保証はありません。
- Node状態は30秒周期。Gatewayは新しい状態または計測の受信から120秒でofflineとし、5秒周期で判定します。Node別のMQTT LWTは使用しません。
- 登録・解除・ID変更はGatewayの永続desired revisionとNode NVSの適用revisionを照合します。未完了設定はGatewayが再送します。
- SEN66は物理Node IDの内部topicへ代理送信します。Gatewayで所有者/revisionを確認してから従来の測定topicへ転送するため、BLE Sensor ManagerはSEN66運用にも必要です。

具体的な責務、削除した機構、限界は[設計](../../../docs/decisions/esp-wifi-mesh-node-networking.md)、移行手順と検証結果は[移行資料](../../../docs/developer/native-mesh-migration.md)を参照してください。

## 計測とBLE

SEN66のドライバ、I2C自動検出、計測継続、sensor単体の復旧を維持します。通信queueが受け付けない場合はその測定値を破棄し、計測を続けます。測定連番は成功したreadごとに進みます。

BLE Discovery v1とNode IDは維持します。SwitchBotのraw manufacturer/service dataをRoot経由でGatewayへ送り、既存Gateway decoderが通常topicへ変換します。

- Active scan: interval 100ms、window 50ms。Scan Responseが必要な機種の収集を維持します。
- 環境値の転送は最短25秒。Motion/Contact等の認識済み状態変化は即時送信し、現在状態も最短10秒で送信します。
- BLE callbackは通信を待ちません。イベントの一時保留は機器ごとに2件・30秒までで、無制限の履歴配送はしません。
- Wi-Fiはcore 0、BLE controller/hostはcore 1、software coexistenceを有効にします。RF共存とscanの非受信時間に起因するイベント取りこぼしは残ります。

## Node identity・登録・USB

`node_id`はeFuse base MACから導く従来の12桁hex IDです。Wi-Fi credential、PoP、factory partition、USB provisioning v2を維持します。BLE relay専用Nodeはlogical ID未登録でも動作します。

登録はNVSの単一blobにrevisionとlogical IDを保存します。旧`registered`/`logical_id`は初回読取時にrevision 0として扱い、最初の変更で新形式に保存します。解除もrevision付きtombstoneです。旧firmwareへ戻しても新しい登録状態が読めるとは限りません。

初回setup・通常更新・再セットアップの違いは[Node導入](../../../docs/user/esp32-node-setup.md)と[保守](../../../docs/user/node-maintenance.md)を参照してください。通常更新はNVSとfactory secretを消去しません。開発用登録resetは新しい解除revisionを保存します。ネットワーク診断用`OMKDIAG`・lifecycle試験コマンドはありません。

## Offline build / test

```sh
cd firmware/esp32/omk-node
pio run -e atom-s3-lite
python -m pytest tests
```

Gatewayは`services/ble-sensor-manager/tests`を実行します。buildはuploadを含みません。`m5stick-c`は従来board設定を残していますが、native USB Serial/JTAGを持たず、このUSB setup経路は利用できません。保存済みWi-Fi設定でのbuild対象に限り、別boardの実機動作を保証するものではありません。

旧版とnative版はMesh ID/PSKの導出ドメインを分離しています。既存Wi-Fi credentialは再利用できますが、旧Nodeを新Nodeの中継器として使えません。Gatewayサービスと経路上の全Nodeを揃えて移行してください。保存済みprebuiltはこのソースへ自動更新されません。
