# 通信障害によるMCU再起動の撤去（第2段階）

基準は `7f97f80`。通信不達はMeshとMQTTの既存復旧経路へ任せ、
計測を継続する。USB設定操作による明示的な再起動、IDFのpanic/watchdogは維持する。
ビルド・ホスト試験は実機での復旧保証ではない。

## 実装と互換性

- `mesh_recovery.c/.h` は親再選択、router再接続、Root voteの選択と
  世代・役割検証を維持し、restart/rearmアクションと予算依存を撤去した。
  自律復旧猶予120秒＋既存jitter/2、明示的再選択間隔300秒＋jitter/2、
  子のIP待ち180秒・再選択間隔300秒は変更しない。
  旧予算による失敗後の抑制はなくなり、従来の未消費予算時と同じ経路を使う。
  API要求の30秒間隔・pending最大1件・既存Root grace/閾値は維持する。
- `mesh_network.c` から再起動予約、NVS保存、再検証、再arm、
  イベント処理停止時の再起動fallbackを撤去した。
- `mesh_recovery_store.c` は `omk_recovery/budget_v1` の旧16バイト形式を
  読み取り専用で維持する。作成・書込み・commit・read-back・消去はしない。
  欠落・不正データ・読取り失敗は現在の復旧判断に影響しない。
- `boot_diagnostics` はboot_count更新と実際のreset reasonを維持する。
  `last_omk_reason`、`mesh_mqtt_rc` は旧値を読み出すだけで、新たに記録しない。
  `recovery_restart_count` は旧予約保存時の累積値であり、実際に再起動した回数や
  今回の障害原因を保証しない。`last_omk_restart_reason` も過去の履歴である。
- MQTT statusのフィールド名・型を維持する。`restart_budget_spent`、
  `restart_retry_ready`、`restart_persistence_blocked` は常にfalse。
  `last_restart_reason` と `recovery_restart_count` は旧NVSの履歴。
  現在の `recovery_reason` は旧NVS値から初期化しない。
- stage 0～5、action 0～3、reason番号は変更しない。
  廃止stage 6/7とaction 4/5は再利用しない。新しい診断の予算bit 128は0。
  RTC保存スナップショットは変更しないため、旧番号やbitを含む過去データが
  再送される可能性は残る。現在の動作として解釈しない。
  Gatewayのdata-transformerはmesh_recovery/statusを通常のstatusとして処理し、
  stage番号の固定列挙を要求していない。Gateway変更はない。

## 復旧経路と限界

Meshのself-organized設定、親探索、再接続、Root選挙、既存Root品質補助は維持。
IP取得・喪失のイベント処理、MQTTクライアントのstart/reconnect、
再接続時のNode登録・設定購読も変更しない。無線SDK内部の選挙成功・所要時間は
ホスト試験で保証できない。

固定されたESP-MQTT 1.1.0の `mqtt_client.c` では、transportエラーや
PING応答タイムアウトは `esp_mqtt_abort_connection` からWAIT_RECONNECTに入り、
既定10秒後の再試行へ進む。既定keepaliveは120秒。
IP復帰時には既存 `start_or_reconnect_mqtt` が再接続を要求する。

**PINGRESPは返るがPUBACKが返らない場合、自動再接続は保証されない。**
QoS再送とoutbox期限切れ削除は動作するが、期限切れだけでは接続を切らない。
probeは診断として継続し、期限切れ通知でpendingを解放できる。
既存の `esp_mqtt_client_reconnect` による接続再確立が役立つ可能性はあるが、
Broker側の異常が残れば成功しない。今回は独自監視・強制再接続を追加しない。

Mesh API、イベントループ、MQTT taskが永久停止した場合、MCU再起動による
脱出経路はなくなる。計測継続もCPU・スケジューラ・センサが動作する範囲であり、
全障害で保証するものではない。最新1件mailbox、TX固定4枠、outbox上限、
backoff、SEN66/BLE、netif寿命管理、SDK/依存・パッチは変更しない。

## 検証

```sh
OMK_TEST_CJSON_DIR="$PWD/firmware/esp32/omk-node/managed_components/espressif__cjson/cJSON" .venv/bin/python -m pytest -q firmware/esp32/omk-node/tests
cd firmware/esp32/omk-node
/home/omkdev/.local/bin/pio run -e atom-s3-lite
```

356件成功、失敗・skipなし。主な回帰範囲：

- 実ポリシー/dispatcherの長時間Broker停止、PUBACK停止、IP未取得、親切断、Root孤立。
- 旧NVS予約・不正データ・読取り失敗、複数boot相当の再初期化でも書込み/再起動なし。
- 世代・役割変更時の古い要求排除、Root vote/grace/cooldown、キュー満杯時の要求頻度。
- 実計測/mailbox/送信・登録処理を使い、1時間断と復帰を3回反復。
  1,080回の計測を継続し、復帰ごとに最新1件を送信。不要なセンサresetなし。
- PUBACK停止中の20分・120回の計測継続と、その後のACK・最新値送信の復帰。
- ESP-MQTT実ソースのkeepalive/abort/reconnectを時刻・transportスタブで検証。
  24時間相当のPINGRESPのみでは再接続しない条件も確認。
- 既存USB provisioning試験と明示再起動経路の静的チェック、status payload互換性。

実機操作は実施していない。ヒープ断片化、stack余裕、長期RF安定性は未測定。

## 複雑性と資源

行数は変更した本番9ファイル（コメント・空行を含む物理行）で比較。

| 指標 | 第1段階 | 第2段階 | 差分 |
|---|---:|---:|---:|
| 本番9ファイル行数 | 1,411 | 1,125 | -286 |
| 有効action（NONE含む） | 6 | 4 | -2 |
| 有効stage | 8 | 6 | -2 |
| PlatformIO Flash bytes | 1,566,253 | 1,563,589 | -2,664 |
| 静的DRAM bytes | 79,360 | 79,216 | -144 |
| タスク・キュー・mutex生成箇所 | 基準 | 同数 | 0 |

削除関数は `restart_still_valid`、`mesh_recovery_restart_valid`、
`mesh_recovery_store_save`、`mesh_mqtt_liveness_should_recover`、
`boot_diagnostics_record_restart_reason` の5つ。
arbiterから11メンバ（予算、healthy/MQTT待ち、retryと各時刻・切断回数）、
入力から2メンバ、networkから6グローバル変数、旧liveness入力構造体を撤去。
再起動/再arm専用の6種類の時間定数（MQTT180秒、IP600秒、孤立600秒、
retry600秒、healthy900秒、settle180秒）と判定・再arm要求の時刻管理を削除。
再起動用jitterの用途は削除し、親再選択にも使うhash/jitterは同一値で維持する。
RTOS timerを削除したという意味ではない。新しい実行資源は追加していない。
旧NVSレコードの読取り構造体と診断履歴は互換性のため残す。

成果物のmanifest、ソースarchive、検証ログ、チェックサム、資源比較は
第1段階とは別の保全ディレクトリに保存する。公開prebuiltは更新しない。
