# ESP-WIFI-MESHを共通Nodeの通信層として採用

- **決定日:** 2026-08-20
- **ステータス:** 採用
- **production対象:** AtomS3 Lite

## 決定

共通Node firmwareの通信層としてESP-WIFI-MESH internal IP networking/NAPTを採用する。1台のNodeはSEN66計測、BLE scan/SwitchBot relay、Mesh中継、通常TCP MQTT publishを同時に担う。root/parent/childは固定せず、保存済みGateway SSID/PSKを共通入力としてMeshが自動選択・自動再構成する。

Meshはrootを頂点とするtree topologyであり、各childはparentを経由する。rootはGateway APへ通常STA接続し、internal subnet `10.0.0.1/16`のDHCP/DNS/NAPTを提供する。childはMesh parent経由でDHCP接続し、rootのNAPTを通じて`mqtt://192.168.50.1:1883`へ通常TCP接続する。Gatewayには特別なMesh daemon、routing daemon、独自relay protocolを追加しない。市販Wi-Fi中継機は必須にしない。NodeはAC電源前提である。

## credential導出

Mesh IDとMesh AP passwordはsource固定値、NVSの別平文値、Node固有値ではない。保存済みGateway credentialから次の固定バイト列をSHA-256へ入力してruntime導出する。

```text
SHA-256("OMK-MESH-ID-v1" || NUL || SSID bytes || NUL || PSK bytes)[0:6]
SHA-256("OMK-MESH-PSK-v1" || NUL || SSID bytes || NUL || PSK bytes)[0:16]
```

`NUL`は1 byteの`0x00`を表す。後者の16 byteはlowercase hex 32文字をMesh AP passwordとして使う。SSID、PSK、hash入力、導出値はログへ出さない。決定的な非秘密test vectorをfirmwareとhost pytestで検証する。

Gateway SSID/PSKはNVS namespace `omk_net`、key `gw_cred`のversioned recordとしてread-back検証付きで保存する。これはESP-WIFI-MESHがroot router接続やchild parent接続に使うruntime `WIFI_IF_STA` configurationと意図的に分離する。新recordがない旧Nodeは、legacy STA configがBSSID固定でなく、かつ既定Mesh内部AP名でもない場合だけ一度移行する。内部parent stateまたは判定不能な値は移行せず、USB再Provisioningを要求する。

この安全側migrationでは、credential分離版へ更新した既存NodeにUSB再Provisioningが必要になる場合がある。特にMesh runtimeが旧`WIFI_IF_STA`へ親Nodeの内部AP情報を書いた後は、その値をGateway credentialとして採用しない。GatewayへNodeをUSB接続して次を実行し、`USB provisioning confirmed for node_id=...`を確認する。

```bash
python3 scripts/provision_omk_node_via_usb.py --device /dev/ttyACM0 --profile omk-ap
```

この復旧手順はNodeの物理ボタン操作やUSB抜き差しを通常のreboot手段として要求しない。

## 診断と運用上の解釈

diagnostic topicと全fieldは[データ経路とMQTT仕様](../developer/data-and-mqtt.md#mesh診断status)で定義する。`parent_disconnect_count`はraw Mesh event回数であり、利用者が知覚する通信断回数ではない。起動やtopology再構成時に複数回増加してよい。`mesh_layer=-1`や旧parent BSSIDなどのtransient観測値も起こり得る。parent切断時のcurrent IPは`0.0.0.0`へclearする。現行BLE managerのNode online判定はDiscovery BLE、raw relay、SEN66の受信鮮度を使い、このMesh診断topicによるstabilization判定は実装していない。

Mesh通信が成立しているのにMQTTだけが長時間復旧しない状態では、software restartで復旧を試みる。Mesh起動済み、parent接続済み、rootlessでない、有効IPあり、MQTT client開始済み、MQTT未接続の条件がすべて180秒連続した場合だけ発動する。単にMQTTが180秒未接続であることを条件にしないため、Mesh再構成中や通常の通信断はrestart対象にならない。parent切断、rootless、IP未取得、MQTT接続復旧、その他の通常通信条件の喪失で判定時間をリセットする。restart直前にはMesh/MQTT診断情報をログへ出し、`mesh_mqtt_liveness_timeout`をNVSへ記録してからrestartする。`last_omk_restart_reason`と`mesh_mqtt_liveness_restart_count`で、直近の理由と累積回数を診断できる。

実住宅試験では、同一SSID・異BSSIDのGateway／市販Wi-Fi中継機が同時に見える環境で、別root/treeが形成されるリスクを確認した。また、非同期起動やNode移設後に最適rootへ自動復帰するとは限らない。採用当初は診断収集を先行したが、現在は下記の条件付きroot再選出を実装している。市販中継機との併用や配置ごとの最適性は引き続き実機評価対象である。

WPA2暗号化Mesh APのassociation expiryはESP-IDFの推奨に従い30秒とする。router BSSIDの固定やmanual parent選択は行わない。同一Meshの複数root競合は`esp_mesh_allow_root_conflicts(false)`で無効化している。

## 現行のroot再選出とlivenessの関係

`mesh_root_recovery.c`は、rootでの`MESH_EVENT_ROUTING_TABLE_ADD`後に60秒の安定待ちを置く。待機中の追加参加は待ち時間を延長する。また、30秒ごとの診断でrootの有効RSSIが−80 dBm以下で、前回観測からの増分が「parent切断3回以上」または「MQTT切断2回以上」の場合も再選出を要求する。RSSIだけでは要求しない。

実行時にもroot・parent接続済み・rootlessでないことを要求し、通常の制御経路から`esp_mesh_waive_root(NULL, MESH_VOTE_REASON_ROOT_INITIATED)`を呼ぶ。成功後は15分のcooldownを置き、その間の追加要求を抑止する。再選出成功後120秒はMQTT livenessによるsoftware restartを抑止し、再構成を優先する。その後も通常通信条件が揃ったままMQTT未接続が180秒継続した場合だけ既存のrestart判定へ進む。

これはchildのRSSI roamingや特定Nodeへのroot固定ではなく、最適なrootへの交代を保証しない。再選出の要求・実行・延期理由はログで確認する。下記の過去のフェイルオーバー試験と、この追加policyの実機検証は区別する。policyの条件・cooldown・時刻wrapはホストテストで確認する。

## 実機確認済み範囲

- AtomS3 Lite単体のroot/layer 1、Gateway MQTT、30秒status publish
- BLE relayとMesh/MQTTの同時動作
- SEN66、BLE relay、Mesh/MQTTの同時動作
- AtomS3 Lite 2台のroot/layer 1とchild/layer 2
- 遠方layer 2から約10秒周期のSEN66とBLE relay同時publish
- layer 2 RSSI約`-75`〜`-77 dBm`での継続通信

AtomS3 Lite 2台で、root/layer 1とchild/layer 2、childからのSEN66約10秒周期publish、registration status/ack、BLE relay、Mesh/MQTTの並行動作を確認した。root/child役割、MAC、IPアドレスは固定割当仕様ではない。既存Nodeでcredential分離用のNVS recordがない場合は、USB provisioningで`omk_net/gw_cred`を保存してから接続する。

### Gateway AP一時消失後の自動復旧

Gateway APの一時消失・復帰後も、root external STAはDHCP、IPv4、MQTTを自動再確立することを確認済みの採用要件とする。root/parent/childの固定、特定MACへの依存、固定IP割当をこの要件の実現方法にしない。childのinternal DHCPとrootのDHCP/DNS/NAPT経路は維持する。

root parent再接続時は`esp_netif_action_connected()`でnetifをupにし、DHCP clientを開始する。parent disconnect時にはdiagnostic statusのcurrent IPを`0.0.0.0`相当にclearする。Gateway APの一時消失・復帰後、rootのDHCP、IPv4、MQTT、SEN66 publish、BLE relay publishが自動復帰することをAtomS3 Lite 2台で確認した。Node再起動、USB操作、手動再Provisioningは不要である。

検討したnon-rootからraw Mesh application packetでrootへ送りMQTT relayする方式は採用しない。Espressif `ip_internal_network`方式でchildが通常LwIP/TCP/IPとESP-MQTTを使う構成が実機で成立したためである。

## 初期検証から移管した知見

初期検証から得た必要な知見、すなわちdefault STA netifをWi-Fi初期化前に作る順序、`esp_mesh_start()`成功後のreceive task開始、childからrootへの`MESH_PROTO_AP`/`MESH_DATA_TODS` frame転送、rootからchildへの`MESH_PROTO_STA` frame転送、root外部IP取得後のinternal DHCP/DNS/NAPT開始は`firmware/esp32/omk-node/`へ移植済みで、host testで重要な順序とframe経路を回帰確認する。

routing-table配布、button入力、`/topic/ip_mesh` demo publish、固定Mesh credentialを含む実験固有処理は本番要件ではなく、移植しない。本番仕様は`firmware/esp32/omk-node/`とこのdecisionに記載する。
