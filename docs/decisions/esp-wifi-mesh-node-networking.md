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

Mesh/MQTTの復旧判断は、全Node共通の`mesh_recovery.c`に集約する。MQTT-onlyの180秒liveness条件は維持し、parent lossと共通の永続restart budgetを使う（下記）。rootlessはtreeにrootがない状態であり、ローカルparentのSTA切断とは別に保持する。

実住宅試験では、同一SSID・異BSSIDのGateway／市販Wi-Fi中継機が同時に見える環境で、別root/treeが形成されるリスクを確認した。また、非同期起動やNode移設後に最適rootへ自動復帰するとは限らない。採用当初は診断収集を先行したが、現在は下記の2段階root recoveryを実装している。市販中継機との併用や配置ごとの最適性は引き続き実機評価対象である。

WPA2暗号化Mesh APのassociation expiryはESP-IDFの推奨に従い30秒とする。router BSSIDの固定やmanual parent選択は行わない。同一Meshの複数root競合は`esp_mesh_allow_root_conflicts(false)`で無効化している。

## 標準parent selection・RSSI policy（2026-10-06）

最優先はSEN66/BLEの長期データ到達性である。ESP-WIFI-MESH標準のpreferred parent選択は候補の浅いlayerを優先し、同じlayerではchild数の少ない候補を優先する。OMK独自の最小layer最適化は追加しない。候補にRSSI thresholdがあることと、`mesh_rssi_threshold_t.low`が厳密な候補足切り値であることは同義ではない。ローカルheaderが説明するhigh/medium/lowはparent間のRSSI range比較と継続的なweak RSSI通知の境界である。

AtomS3 Liteを含めhigh/medium/lowをESP-IDF標準の **−78/−82/−85 dBm** に統一する。Atom固有の−78/−80/−82を廃止し、実住宅で通信できた−84〜−85 dBm付近を過度に厳しく扱わない。これは任意の−85 dBmリンクの採用・維持を保証しない。

### IDF 6.0.1で確認したAPIと確認限界

実装前にローカル`framework-espidf/version.txt`、`components/esp_wifi/include/{esp_mesh.h,esp_mesh_internal.h}`、`examples/mesh/{ip_internal_network,internal_communication,manual_networking}/main/mesh_main.c`を確認した。Mesh実装は`components/esp_wifi/lib/esp32s3/libmesh.a`のbinary配布で、parent switching内部sourceは含まれない。公開header・exampleと[同版の公式self-organized networking仕様](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32s3/api-reference/network/esp-wifi-mesh.html#enabling-self-organized-networking)を根拠とする。

| API・型・event | 確認した意味／今回の扱い |
| --- | --- |
| `mesh_switch_parent_t.duration_ms` | parentの弱RSSI監視時間。継続すると新parentを探索する |
| `cnx_rssi` | parent接続のRSSI閾値。−120 dBmより大きい設定で監視timerがarmされる |
| `select_rssi` / `switch_rssi` / `backoff_rssi` | headerはそれぞれ選択閾値／切断・切替閾値／root接続閾値と説明し、`select_rssi > switch_rssi`を要求する。ただし改善幅や内部相互作用・全制約は確定できない |
| `esp_mesh_get_switch_parent_paras()` / `esp_mesh_set_switch_parent_paras()` | read/set APIは存在するが、数値defaultの公開定義はない。setterは使わず、Mesh start後に全fieldとself-organized状態をread-back/loggingする。実機を操作しない今回、実測runtime defaultは未取得 |
| `esp_mesh_set_rssi_threshold()` | headerに標準−78/−82/−85が明記されている。設定後read-backする |
| `esp_mesh_set_self_organized(true, true)` | non-rootはpreferred parentを選び直す。接続済みなら一度切断する。rootはrouterとchildを切断しroot roleを放棄する |
| `esp_mesh_set_self_organized(true, false)` + `esp_mesh_connect()` | root roleを維持してrouterへ再接続する公式手順。前段が失敗したらconnectは呼ばない |
| `PARENT_CONNECTED` / `PARENT_DISCONNECTED` | ローカルSTA parentの接続／切断。rootではrouter。切断payloadは`wifi_event_sta_disconnected_t`のalias |
| `NETWORK_STATE` | `mesh_event_network_state_t.is_rootless`。rootless解除をSTA再接続eventの代用にしない |
| `NO_PARENT_FOUND` | `mesh_event_no_parent_found_t.scan_times`。回数・最後のscan_times・孤立中の理由を記録する。eventごとに猶予timerを延長しない |
| `STOP_RECONNECTION` | headerはrootのrouter再接続／non-rootのparent再接続停止と定義する。exampleはログのみで、詳細な発生条件はbinary内のため未確定。回数・孤立中の理由を記録し、接続状態と経過時間と併用する。単独eventで切断扱い・再起動しない |

self-organized networkingは標準で有効で、root選択・preferred parent選択・再接続を担当する。OMKでswitchパラメータを設定していなかったことは、標準parent switching無効の根拠にはならない。−82 dBm前後・30〜60秒・明確な改善幅という独自hysteresis設定は、今回の情報だけでは安全に定義できないため採用せず、runtime値を維持する。標準動作と同じ周期の探索をOMKで追加しない。

manual networking、`esp_mesh_set_parent()`、applicationからのWi-Fi scan/connect/disconnectは追加しない。独自parent選択にはscan制御、layer、loop防止、child capacity、root整合、self-organizedとの排他まで必要になる。実住宅評価後も不足する場合に限りPhase 2としてparent scoringを検討する。

## 全Nodeのparent-loss recoveryとbounded restart

30秒のstatus周期で`mesh_recovery.c`が1つのactionを選ぶ。Mesh/IP eventと周期処理をdefault event loopへ直列化し、timer callbackはeventをpostするだけにする。切断・rootless・IP lossも観測へ渡し、周期間の短い断絶で正常継続時間が誤って繋がらないようにする。MQTTはdisconnect counterの変化も見る。

- `healthy`: Mesh起動済み、parent接続、非rootless、有効IP、MQTT接続。parentだけ戻った場合は`wait_ip`または`mqtt_wait`であり、正常扱いしない。
- `self_healing`: 起動後parent未接続、parent切断、rootlessで開始。短い断絶ではOMKからAPIを呼ばない。parent接続かつ非rootlessでloss timerとexplicit状態をclearする。
- `explicit`: 猶予後、non-rootは`true,true`、router未接続rootは`true,false`＋`connect()`。rootがrouterに接続したままrootless通知を受けた場合はroleを放棄しない。実行直前にもrole・接続状態を確認する。
- `restart_pending` / `budget_hold`: 下表の時間と成功したexplicit要求が揃った場合のみrestart候補になる。budget消費済みなら再起動せず低頻度の再選択を継続する。APIが失敗し続けて要求を受け付けられない場合もrestartしない。

| 条件 | 時間 | 選定理由 |
| --- | --- | --- |
| self-healing猶予 | 120秒＋ID jitter 0〜60秒 | 短い断絶・標準再構成を待つ |
| explicit成功後の次回探索 | 300秒＋同0〜60秒 | 探索を繰り返して収束を妨げない |
| parent/rootless restart | 孤立600秒＋ID jitter 0〜120秒、かつ最後のexplicit成功後180秒以上 | 再選択に十分な猶予を与えた最終fallback |
| budget消費後 | 300秒＋同0〜60秒で探索。API失敗でも同間隔 | 届かないNodeでもrestart loopやAPI stormを起こさない |
| budget回復 | `healthy`が900秒連続 | root recoveryの15分cooldownと揃え、一時復帰では再armしない |
| MQTT-only | 既存の適格条件で180秒＋ID jitter 0〜120秒 | broker断時にも同時再起動を減らす |
| 未受付API失敗 | 次のstatus周期で再試行 | 成功時のgrace/cooldownを誤って消費しない |

時刻はunsigned差分で32-bit ms wrapを扱う。ID全byteのhashからjitterを決定し、全Nodeを同じtimeoutで再起動させない。hash衝突や30秒周期への丸めによる同時動作の可能性は残る。event起点の判定からactionまでは最大1 status周期が加わり、起動時未接続は最初の周期で観測する。

NVS `omk_recovery/budget_v1`の単一versioned blobに、消費済みflag・累積restart予約回数・最後の理由を保存する。**commit後にhandleを開き直してread-back一致した場合だけ** `esp_restart()`へ進む。NVS open/read/format/write/commit/read-back失敗はrestartを禁止し、RAM budgetも閉じる。初回のkey未作成だけを新規budgetと扱う。再起動直前に電源断しても、markerが永続化済みならbudgetは消費したままとなる（予約回数と実際のsoftware reset回数はこの境界で一致しない場合がある）。

parent lossとMQTT-onlyは同じbudgetを消費する。再起動、role変更、短いparent/MQTT復帰、理由の変化ではbudgetを回復しない。900秒正常継続後、NVSへのclear・read-backが成功して初めて次episodeの1回を許可する。再起動後も孤立なら同episodeのまま探索を継続する。NVS初期化・factory eraseはこの保証の範囲外であり、自動recoveryではNVSを消去しない。

優先順位は **parent/rootless → weak-root Level 2 → root Level 1 → MQTT-only**。上位の猶予中も下位へfall-throughしない。root router再接続の2 APIは1つのactionとして扱い、失敗時も同じ周期に別actionを呼ばない。budgetの再armもこのarbiterで行う。

## 既存2段階root recoveryとの統合

`mesh_root_recovery.c`は、30秒ごとのstatus処理でGatewayリンクを観測し、次の2段階を同じ制御経路から選択する。実行時にもroot・parent接続済み・rootlessでないことを確認する。

- **Level 1: root再選挙。** rootでの`MESH_EVENT_ROUTING_TABLE_ADD`後に60秒の安定待ちを置く。待機中の追加参加は待ち時間を延長する。また、rootの有効RSSIが−80 dBm以下で、前回観測からの増分が「parent切断3回以上」または「MQTT切断2回以上」の場合も要求する。`esp_mesh_waive_root(NULL, MESH_VOTE_REASON_ROOT_INITIATED)`で、より良いroot候補への交代を試みる。候補がなければ現在のrootが残るため、この段階では弱RSSIだけを理由に投票しない。API成功後はLevel 1に15分のcooldownを置く。
- **Level 2: root放棄とparent再探索。** rootかつRSSI validかつGateway RSSIが−85 dBm以下の観測が180秒継続した場合、`esp_mesh_set_self_organized(true, true)`を呼ぶ。ESP-IDF 6.0.1ではrootがrouterおよびchildとの接続を解除し、root roleを放棄して通常Nodeとしてpreferred parentを探索する。parentの選択はESP-WIFI-MESHへ任せ、OMKによるparent固定や独自の周辺RSSI scanは行わない。短時間の切断回数やLevel 1の実行有無は、この段階の必須条件にしない。

Level 2の継続時間とcooldownはLevel 1と分離する。Level 1を実行しても継続時間はリセットせず、たとえば最初の弱RSSI観測を`t=0`として、`t=30秒`のLevel 1後もroot／−89 dBmのままなら`t=180秒`でLevel 2へ進める。RSSIが−85 dBmより改善、non-root化、RSSI invalidのいずれかをstatus処理で観測すると継続時間をリセットする。最初の該当観測から180秒を数えるため、物理的な劣化開始からの遅れには最大1 status周期が加わる。

同じ周期で両段階が成立した場合はLevel 2を優先し、API失敗時にも同じ周期でLevel 1を続けて呼ばない。Level 2成功後は専用の15分cooldownへ入り、継続時間と未実行のLevel 1要求をリセットし、Level 1にも15分のcooldownを適用して再構成を保護する。cooldown中もリンク観測を続け、終了時に再び180秒の継続条件を満たしていれば再実行できる。Level 2 API失敗では成功時刻・cooldown・graceを更新せず、条件が続けば次の30秒周期で再試行する。時刻比較は`uint32_t`のwrapを考慮する。

どちらのAPI成功後も120秒はparent-loss actionとMQTT-only restartを抑止する。通常通信条件が失われればliveness計測をリセットし、grace後も上記の共通budget・jitterを通してのみrestartを許可する。APIの`ESP_OK`は回復要求の成功として扱い、実際のroot交代・parent接続・MQTT復旧の完了を意味しない。

Level 1は`root reelection requested/executed: ... (level=1)`、Level 2は`root recovery requested: sustained_weak_root (level=2)`と`root parent reselection executed: sustained_weak_root (level=2)`で区別する。失敗時は理由と`esp_err_to_name()`によるESP-IDF errorを記録する。既存`omk/node/<node_id>/status`の1152-byte上限とfieldを維持し、追加診断は30秒周期の`omk/node/<node_id>/mesh_recovery/status`（768-byte buffer、QoS 0、非retain）へ分ける。stage、最後の理由、no-parent/stop/reselection回数、scan_times、loss秒数、budget、永続restart予約回数と理由を送る。孤立中はGatewayへ送れないため、復帰後のstatusとbootを跨ぐrestart理由を併用する。data-transformerはこのtopicをstatusとして扱い、計測変換エラーにしない。

[ESP-IDF 6.0.1のself-organized networking仕様](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32s3/api-reference/network/esp-wifi-mesh.html#enabling-self-organized-networking)と同版の`esp_mesh.h`の型・利用条件に基づく。特定treeへの統合や最適rootへの交代を保証するものではない。root conflicts無効化と最大layerは維持し、Atom固有RSSI閾値は今回標準値へ戻した。下記の過去のフェイルオーバー試験と、この追加policyの実機検証は区別する。継続時間のリセット、段階間の優先順位、API失敗、cooldown、liveness grace、時刻wrapはhost testで確認する。実機では弱rootとそのchildの再接続、既存treeへの統合可否、MQTT／SEN66／BLE relay復旧、15分以内の再探索抑止を確認する。

## 今回の根拠と未検証範囲

実住宅でroot `09dda0d5a8f2` → layer2 `55f94c790e12` → layer3 `9490b46aee0d`（`sen66-002`）が成立し、前者リンクが約−84〜−85、後者が約−63〜−65 dBmだったという報告を入力とする。後に後二者が消失し、layer2物理再起動後も2分超復帰せず、別のroot/childは通信継続した。

コードから確定できるのは、旧livenessが`parent_connected=true`を要し、parent未接続の長期孤立を処理しなかったこと、Atom profileが標準より厳しかったこと、旧MQTT restartに永続budgetがなかったことである。実際の切断trigger、候補不足、scan/reconnect停止、別tree形成、IP/NAPT停滞のいずれが原因かは、報告だけでは確定しない。今回のpolicy・時間定数・parent switching runtime値は実機未検証であり、以下の過去確認済み項目からその効果を推定しない。

host testは短い断絶、起動時孤立、再選択成功／失敗、同episodeの再起動抑止、NVS障害と複数boot、正常900秒の再arm、途中のIP/parent/rootless/MQTT断、role変更、Level 1/2/MQTT排他、event連発、ms wrap、ID jitter、実際のdiagnostic formatter容量を対象とする。NVS実デバイスでの電源断耐性やRF挙動はhost stubだけでは検証できない。

次回の最小実機確認は、起動時のRSSI/switch read-back、短いparent断で無介入、長いparent断とGateway/router断でAPI分岐が正しいこと、圏外継続でboot_countが1回だけ増加すること、復帰後のMQTT/SEN66/BLEのデータ受信と900秒後のbudget回復である。MQTT-onlyのbroker断も共通budgetで最大1回となることを確認する。新しい良好なparentの存在・別tree統合・無欠測は保証しない。parent接続済みかつ非rootlessのままIP未取得だけが永久に続くケースは`wait_ip`として診断し、この変更では新たなDHCP再起動policyを追加しない。

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
