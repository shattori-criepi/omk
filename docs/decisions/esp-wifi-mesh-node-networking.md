# Native ESP-WIFI-MESH通信

2026-11-10。IP/NAT方式を置換する実装判断。実機未導入。

## 決定

非Rootの仮想netif・DHCP・TCP/MQTTを廃止し、公式Mesh APIによるNode→Root→Gateway通信へ移行する。Rootは自動選択とし、BLE/SEN66は全Nodeで継続する。SDKのMesh形成、経路選択、再接続を使用し、独自の親選択や通信停止時の再起動を加えない。

参照はESP-IDF 6.0.1の[Mesh API](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32s3/api-reference/network/esp-wifi-mesh.html)、[公式internal_communication](https://github.com/espressif/esp-idf/tree/v6.0.1/examples/mesh/internal_communication)、[Wi-Fi/Bluetooth共存](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32s3/api-guides/coexist.html)。`ip_internal_network`はIP透過転送を実現するサンプルであり、native Mesh転送にDHCP/NAPTは必須ではない。

## 責務

| 場所 | 標準機能 | OMKが実装する処理 |
| --- | --- | --- |
| 全Node | ESP-WIFI-MESH自己組織化、送受信、中継 | センサ値JSON、有限queue、30秒状態、登録revision適用 |
| 現Root | 公式STA netif、DHCP client、ESP-MQTT再接続/outbox | 固定message kindから既存topicへ変換、Node ID→MACの対応、下り設定の転送 |
| Gateway | MQTT retained/QoS 1、既存decoder/HTTP API | 最新希望状態の永続化・再同期、SEN66の所有者/revision照合、Node受信鮮度から可用性をpublish |

上りは`esp_mesh_send(NULL, ...)`、Rootで`esp_mesh_recv()`。下りは学習済みMAC宛へ`esp_mesh_send()`し、対象Nodeで同じ受信APIを使う。中間Nodeにアプリケーション転送を作らない。IP/socket転送をしないため`esp_mesh_recv_toDS()`は不要。送信APIは一つのtaskから直列に呼ぶ。

全NodeにRoot用の標準STA netifを一度だけ準備する。非RootではDHCP clientを停止し、MQTT clientを作らない。役割が変わると旧MQTT sessionを終了し、新RootがIP取得後に新しいsessionを始める。旧sessionのcallbackと保留データはepochで区別する。これは役割変更の整合性維持であり、SDKの経路選択の代替ではない。

## 保持と配送

周期計測値はQoS 0、アプリケーションqueueは有限・30秒期限。満杯時は新規受付を断り、センサ計測を止めない。RootのMesh受信はMQTT enqueueやNVS保存を実行しない。SDKの送信待ちは専用taskに限定する。Root交代時の旧queueは引き継がない。

JSON最大1024 bytesを20-byteの種別・発信Node識別headerに包む。これは固定形式のフレーミングであり、独自の汎用ACK、配送再試行、分割再構成、経路プロトコルは作らない。MQTTの分割受信だけはESP-MQTTのevent仕様に沿って1024 bytes以内に組み立てる。

Gatewayは登録・解除・logical ID変更の最新希望状態を、正のuint32 revision付きで永続化してからretained QoS 1で送る。Nodeは単一NVS blobへ適用後、実際に保存できたrevisionとIDを返す。同じrevision・同じ内容は再保存せず応答し、古いrevisionや同revisionの競合は拒否する。ACKが失われても30秒状態で収束する。Gatewayの5秒周期で未完了設定だけを再送する。

MQTT QoS 1はBrokerまでの保証でありMesh末端への適用保証ではない。このため制御にはdesired/applied比較が必要だが、測定値へこの機構を適用しない。

Gatewayは解除・変更の適用確認まで旧IDと未確認の要求IDを予約する。RootはSEN66を`omk-relay/<node_id>/sen66/raw`へ送り、Gatewayだけが台帳の現所有者・適用revision・希望revisionを照合して従来の`omk/<logical_id>/sen66`へQoS 0で転送する。旧RootやSDKに残る旧登録世代の計測は共通topicへ出さない。Rootに登録台帳を複製しない。

設定の周期publishはBroker接続中のみ行い、Pahoの送信キューは64件までとする。NodeごとにPahoの送信完了と直近の送信revision/時刻を確認し、未完了publishの複製と5秒未満の同revision再送を抑える。再接続時は永続desired-stateから最新の未完了要求を送る。

## 可用性と互換性

Node状態は30秒周期・非retained。GatewayはNode単位のheartbeat/SEN66/BLEの新しい受信を記録し、120秒無受信ならoffline（判定周期5秒）。retained登録状態、同一bootの重複連番、既知の終了bootのデータは鮮度を延長しない。Gateway再起動後も保存済みの最終受信時刻から120秒の期限を判定し、再起動だけでは鮮度を延長しない。

`omk/<logical_id>/sen66`、BLE raw/通常topic、登録HTTP APIを維持する。論理deviceの`status` publisherはNodeからGatewayへ移す。Node別LWTと大容量の旧診断payloadを廃止する。登録MQTTはprotocol v2へ更新し、revisionを必須とする。USB v2とDiscovery BLE v1は別protocolであり変更しない。

## BLE

Active scanを維持し、100ms interval / 50ms windowへ変更する。環境値は25秒、認識済みMotion/Contact等は状態変化時送信と10秒状態通知を維持する。Wi-FiとBLEのcoreを分けsoftware coexistenceを使用する。SoftAPとBLEのRF共存制約は解消しない。受信できなかった広告は復元できず、変化の保留も2件/機器・30秒に限る。

Presence Sensor ProはGateway対応済みの`0x70`と`00/20`のservice形式を状態変化優先ヒントに含める。Nodeはrawデータを維持し、機種確定・公開値のdecodeはGatewayで行う。

## 削除と残るリスク

削除対象はIP encapsulation、仮想netif/DHCP server/NAPT、netif再生成、Node別TCP/MQTT、独自Mesh/root recoveryとrestart budget、通信診断専用NVS/USB/MQTT、ESP-MQTT patch。

この変更で旧仮想netif/DHCP serverのpanic経路とNode数分のMQTT接続を除去する。Rootの標準DHCP client、Mesh内部queue、BLE/SoftAP共存は残るため、`ESP_ERR_MESH_NO_MEMORY`、親の無応答、RF起因の長時間停止が解決するとは断定しない。

SDK内部の配送滞留時間はアプリケーションの30秒期限で制御できない。Rootでの再受付やMQTT outboxにも個別の期限があるため、端到端30秒保証・exactly once・無欠測は提供しない。多段通信とイベント捕捉の実機確認を合格条件とし、不合格なら独自復旧を積み増さず導入を見送る。

導入と撤退基準は[移行資料](../developer/native-mesh-migration.md)を参照。
