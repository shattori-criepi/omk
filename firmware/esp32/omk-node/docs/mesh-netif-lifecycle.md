# Mesh netifの寿命と初期化（第1段階）

対象は`mesh_netif.c`の寿命管理と部分初期化。各NodeのMQTT、RootのDHCP/NAPT、
固定4枠のMesh TX、5秒の送信待ち、10秒のキュー期限、復旧・再起動方針は維持する。
基準は`021ff22`（既存変更のWIP保存）。

## 所有権と同期

- mainがWi-Fi開始前にstatic mutexを一度初期化する。初期化／役割変更／停止／
  AP開始はmainまたはdefault event loopから呼ぶ。lwIP callbackから呼んではならない。
- mutexはstation/APの公開ポインタ、役割、接続状態、RX generationを保護する。
  RXは`esp_mesh_recv`の**外側**でだけ取得する。受信待ちの解除やRXからの停止ACKを待たない。
- RXはmutexの下で役割とgenerationを検査し、`esp_netif_receive`を呼ぶ。
  切替をまたいだ受信は破棄するため、切替直後の最初の1フレームを捨てる場合がある。
- mutexを保持したままSDKの同期tcpip IPCを呼ぶ。lwIPのTX／free callbackは
  このmutexを取得しない。TX callbackは既存の短い`tx_queue_lock`とコピー処理だけを使う。
  portMUX内でSDKや破棄を待たない。新しいタスク・キュー・タイマーはない。

## receiveのreturn後も残る参照

使用中のESP-IDF 6.0.1で確認した経路（SDKを変更していない）：

1. `components/esp_netif/lwip/netif/wlanif.c:145-185`：入力をPBUF_RAMにコピーし、
   `tcpip_input`へ渡す。元のMesh RXバッファはstatic配列、L2所有ポインタはNULL。
   拒否時はその配列を解放せず、コピー後の投入失敗時はSDKがpbufを解放する。
2. `components/lwip/lwip/src/api/tcpip.c:245-268,442-460`：入力と同期APIメッセージは
   同じ`tcpip_mbox`へFIFO投入される。
3. `components/esp_netif/lwip/esp_netif_lwip.c:239-254,1065-1095,1308-1365`：
   stop／destroyはtcpip threadで同期実行される。アプリ側の新規入力をmutexで止めてから
   stopを投入するので、既に投入された入力が先に処理される。`receive`のreturnだけを
   完了条件にしていない。
4. 公開ポインタを外し、APはNAPTをdisable、stopでDHCP／lwIPの参照を処理し、
   netifをdestroyした**後**にcustom driverをfreeする。処理中の入力からTXが発生しても
   driverはまだ有効。SDKのnetif削除によるARP等の後始末を独自実装しない。
   破棄中のcallbackが作った送信フレームも、破棄完了後のgeneration更新で無効化する。

この説明は`CONFIG_LWIP_L2_TO_L3_COPY=1`、`LWIP_TCPIP_CORE_LOCKING=0`、
`LWIP_TCPIP_CORE_LOCKING_INPUT=0`に依存する。コードにビルド時の検査を置き、
これらを変更した構成で誤って同じ保証を主張しないようにした。

外部STAはEspressifのWi-Fi driverが所有する。stopの後に公式
`esp_wifi_clear_default_wifi_driver_and_handlers`を使い、RX登録、default handler、
driverをSDKに解除させてからnetifをdestroyする。
`esp_wifi/src/wifi_default.c:314-349`と`wifi_netif.c:93-101`で、driver callbackの解除と
Wi-Fi内部RX callbackの登録解除を確認した。閉じたWi-Fiライブラリ内部の実行や無線上の
挙動をホストテストで証明したものではなく、公式detach経路の契約に依存する。
SDKのIP lost timerは`esp_netif_lwip.c:1541-1576`でactive listを検査する。
アプリ側でSDKのタイマーやWi-Fi handlerを重複管理しない。

## 初期化と失敗時の戻り先

新規netifとcustom driverはローカル変数で保持し、attach、MAC、DHCP状態、
APのDNS設定／NAPT開始を確認してから公開する。失敗時はstop→detach／destroy→freeを
通り、公開ポインタを残さない。次の親接続／AP開始等の正当な要求で再試行できる。

default STAではSDK attachがpost_attach前にdriver handleを保持する契約
（`esp_netif_lwip.c:1097-1109`）を使い、attach途中の失敗もSDKのclearで回収する。
Wi-Fi handler登録の途中失敗はSDK自身のrollbackに加え、netif所有分をclearする。

`esp_netif_action_start/connected/stop`はvoidのイベントAPI。
child/rootはconnected後のDHCP状態、APはserver状態とNAPT開始結果を検査する。
default STAのstart直後はまだUPでないのが正常であり、UPを成功条件にはしない。
SDK内部の`ESP_ERROR_CHECK`等のabortをアプリの戻り値で回復可能にしたとは扱わない。
SDK clearの戻り値は有効netifに対するdriver設定解除結果であり、異常なら記録する。

同じinitは既存stationを、同じchild接続は完成したchildを再利用する。
同じroot接続は既存DHCPのconnected actionを使い、STARTEDを再開始しない。
停止成功後は再作成したdefault STAを保持し、重複停止では再確保しない。
停止途中に失敗した場合は停止成功フラグを立てず、次回の停止も再試行する。

Root→child、child→Rootでは所有するdriver種別に従って旧stationを破棄する。
Root APはchild移行／停止時にNAPTを終了してから破棄する。
NAPTにはSDKの同期`esp_netif_napt_enable/disable`を使う。NAPTの転送方針は変えない。

## 遅延IPイベント

IP通知中のポインタは破棄済みの場合があるので直接参照しない。
`mesh_netif_accept_ip_event`は現stationの所有・接続・役割を検査し、現stationから取得した
IPをGOT_IPのpayloadと比較する。LOST_IPは現stationのIPが0の場合だけ受け入れる。
Mesh側とMQTT側の両GOT_IP consumerで使用する。
これらのconsumerと役割変更は同じdefault event loopなので、検査からDNS取得まで
別の役割変更が割り込まない。このAPIを別タスクからnetif貸出用に使用してはならない。

SDKイベントにはアプリのgenerationがない。同一アドレス再利用かつ同じ現IPの場合は
新旧イベントを識別できないが、操作は現stationの状態に対して行われる。
異なる旧役割や旧IPの通知を新状態として適用しない。
DNS取得失敗時にAPを開始しない既存方針と、DNS再試行タイマーがない点は残る。

## 検証

`tests/test_mesh_netif_lifecycle.py`は本番の状態定義、attach callback、RX、
初期化／切替／停止関数を抽出してCコンパイラ＋UBSanで実行する。SDKダブルはコピーと
FIFO IPCの契約を模擬する。pthreadの条件変数で競合順序を固定し、sleepには依存しない。
RX中の停止、破棄中の後続RX、非同期入力中のdriver生存、失敗注入と反復回収、
重複イベント、役割変更、バッファ、遅延IP通知を検査する。
TXの本番callback／workerは既存`test_mesh_tx_queue.py`で別途実行する。

修正前の`mesh_netif.c`を`OMK_NETIF_SOURCE`に指定すると、`rx_destroy`は使用中破棄、
`partial_retry`はattach失敗後の未完成AP公開を検出して失敗する。これはホスト回帰であり、
実SDK／実機上でその競合を再現したという意味ではない。

```sh
OMK_TEST_CJSON_DIR="$PWD/firmware/esp32/omk-node/managed_components/espressif__cjson/cJSON" \
  .venv/bin/python -m pytest -q firmware/esp32/omk-node/tests
# firmware/esp32/omk-nodeで実行
/home/omkdev/.local/bin/pio run -e atom-s3-lite
# repository rootで実行
git diff --check
```

ソース形状の既存検査は共通cleanupへの切り出しに合わせて更新し、従来のDHCP・
初期化順序・役割変更の検査を残した。登録／診断のホストadapterにも新しいIP検査APIを
追加し、拒否イベントがMQTT再接続を起こさないことを検査する。
実行時heap、断片化、stack余裕、長時間通信安定性は実機未検証。
