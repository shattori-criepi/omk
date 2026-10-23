# 実機適用前の最小診断整備

基準は `06c6a0d0f4d146be956c0c86cdd37733b72463be`。通信、復旧判断、
SEN66計測周期、キュー容量、MQTT QoS・再接続・outbox上限は変更しない。
新しいタスク、キュー、mutex、定期送信、NVS領域は追加しない。

## USBで通信停止中の進捗を比較する

既存の `OMKDIAG <view> <page>` の0～26ページは維持する。
追加ページ27～32は **view=0のみ**。応答は改行を含め240バイト未満。
短い既存critical sectionでRAMをコピーしてから整形し、MQTT mutex、
ネットワークAPI、I²C、NVS書込みを呼ばない。未接続SEN66の値は初期値の0。
USB取得によるセンサ初期化、設定変更、再起動はない。

| page | フィールド／配列の順序 |
| --- | --- |
| 27 | `sensor_boot`、`read=[read_attempts, read_success, read_errors, not_ready, last_read_ms, recovery_count, measurement_timeout_count]` |
| 28 | `mailbox=[submitted, replaced, expired, invalidated, unavailable, pending, phase, active_seq, phase_age_ms]` |
| 29 | `probe=[ack_count, ack_ms, pending_id, submitting, fresh]` |
| 30 | `heap=[free_bytes, minimum_free_bytes]` |
| 31 | `boot`、`state=[flags, recovery_us, stage, action]` |
| 32 | `admission=[queue_full, frame_expired, frame_invalidated, mqtt_busy, mqtt_capacity, backoff]` |

各ページの `at` は取得時のboot内uptime（µs）。各ページは別の時刻に取得し、
同一ページ内でも別lockの値は完全な同時値ではない。page 0/31の `boot` は
network diagnosticsの現在boot識別子。page 27の `sensor_boot` は別の乱数で、
最初の読取り試行まで0。両者を同じIDとして結合しない。
`last_read_ms` は最終**読取り開始**時刻であり成功時刻ではない。
探索・初期化試行はread attemptsに入らない。再初期化回数・測定timeoutは
既存 `sen66_rc` / `sen66_to` と同じRAM値を返す。

mailbox phaseは0=idle、1=identity、2=enqueue、3=outbox。
pendingは待機サンプルの有無、active_seqはsender処理中の計測連番。
phase_ageはms。probeのACK時刻はboot内uint32 ms（約49.7日でwrap）。
pending_id=0は未保留、submitting=1は投入処理中。freshは取得したコピーから
既存の判定式で計算し、グローバルな進捗状態は更新しない。
probe ACKはBrokerとのQoS 1進捗であり、SEN66のQoS 0配送ACKではない。

page 31は既存monitorが記録したキャッシュを返す。flagsのbitは
0=Mesh started、1=Root（0なら子）、2=parent connected、3=rootless、4=IP、
5=MQTT started、6=MQTT connected、8=probe fresh。bit 7は予約（現在0）。
started=0のときRoot bitだけで接続役割を確定しない。
`at-recovery_us` でキャッシュの古さを確認する。monitor停止中は値も止まり、
未記録の0は最新状態の保証ではない。stageは0=idle、1=healthy、2=self_healing、
3=explicit、4=wait_ip、5=mqtt_wait。actionは0=none、1=child_reselect、
2=router_reconnect、3=root_vote。時刻・列の詳細は既存READMEも参照する。

実機試験での採取例（今回の開発作業では実行しない）：

```sh
python3 firmware/esp32/omk-node/scripts/capture_network_diagnostics.py \
  --port /dev/serial/by-id/<native-usb-device> --node <12桁node_id> \
  --runtime --once --output /path/to/new-evidence/usb-first.jsonl
# 計測周期を超える間隔（例: 30秒）を空け、同じコマンドをusb-second.jsonlへ実行する。
```

`--runtime` を省略すると従来どおり81ページ、指定時は87ページ。
旧firmwareに `--runtime` を使うと追加ページがtimeoutする。
既存スクリプトはDTR/RTS/resetを操作せず、切断・timeout時に停止する。
native USB Serial/JTAGを使い、給電を切り替えない。通常コンソール行はこの
スクリプトでは保存しない。同じbootでread、mailbox、ACK、admission、
active token/age、recovery_usを2回比較する。再起動を挟む値は差分にしない。

## 受付拒否・破棄の6カウンタ

status `omk/node/<node>/status` に配列 `admission` を追加する。
既存フィールド、sensor payload、送信周期は維持する。配列はUSB page 32と同じ。
uint32累積値でUINT32_MAXに飽和し、MCU起動時に0へ戻る。
MQTT再接続、役割変更、センサ再初期化ではリセットしない。

| 添字 | 実装上の計数条件 |
| --- | --- |
| 0 | Meshの4スロットが占有済み、または待ち時間0のxQueueSend失敗。後者の内部原因は分類しない |
| 1 | workerの既存検査で10秒以上のフレームを破棄。1フレーム1回、broadcast宛先ごとには数えない |
| 2 | 既存検査で送信世代またはRoot/子役割が不一致。両方不一致でも1回。期限切れも同時ならこちらを優先 |
| 3 | bounded enqueue / async subscribeの入口でMQTT recursive mutexを即時取得できず `-3`。outbox読取りのbusyは含めない |
| 4 | 同APIの既存容量事前検査が `-2` を返す。内部enqueueの `-1` を容量不足と推定しない |
| 5 | `mesh_netif_allow_data()` の既存共通backoff判定がfalse。SEN66/BLE等の受付機会の回数で、送信済みpacket数ではない |

キュー破棄は検査時点で数えるため、worker自体が停止中ならまだ増えない。
役割変更でTCP等が失敗するすべてのケースを覆うカウンタではない。
各カウンタは異なる境界を数えるため、合計を測定欠損数として使わない。
高頻度ログや追加送信はない。statusは既存2,816バイト枠で、追加整形・確保が
失敗した場合は従来と同じ基本statusへのfallbackを使う。

## 異常の保持と永続保存

schema 2と既存RTCスロットを維持する。view=0は現在、view=1は固定1件の保持記録、
view=2は既存NVSの最後の記録。現在bootの異常があれば、view=1の過去boot記録より
優先する。同一bootでは重大優先を維持する。
30秒以上実行中だった重大APIのactive情報は、処理完了後の累積maxだけの
snapshotで上書きしない。同じoperationでも新しいactive tokenが30秒に達した
再発は更新対象。同じ実行中tokenは異常maskが従来記録を包含する間更新する。
新しい重大種別が出た場合も更新し得る。全障害履歴を保持する仕組みではない。

NVSの保存済みmask、10分間隔、総計16回、早期異常4回未満の条件は変更しない。
同種・同重大度の現在boot再発はview=1に残ってもNVSには再保存されない。
旧NVSは自動削除しないが、従来の保存条件を満たす新種別・重大度昇格時には
従来どおり最後の1記録として更新される。view=2は不変の永久アーカイブではない。
soft resetではRTC保持を利用するが、電源断ではNVS未保存の現在記録を失い得る。
異常発生中のUSB採取を優先する。

## Gatewayの既存ログを保全・解析する

解析はraw JSONLを使う。Parquet/latestではmeta連番やnetwork diagnosticsの履歴を
保持できない。collectorの購読 `omk/#` はSEN66、Node status、registration、
mesh_recovery、network_diagnosticsを含むが、`omk-liveness/` と `omk-relay/` は含まない。
probe進捗はNode statusの既存フィールドとUSBで見る。
network diagnosticsは5分枠でviewを巡回するため全viewがあれば各view約15分。
QoS 0なので復旧後も回収保証はない。Node statusは既存周期のまま。

以下は**試験時にGatewayで実行する手順**であり、今回実行していない。
リポジトリのGateway設定を前提にし、実機の保存期間・サービス稼働は別途確認する。
まず新規の退避先を作り、試験開始・終了時刻（timezone付き）、NodeとELF、
電源操作の有無を記録する。次は例としてJSTの日付をまたぐ窓を扱う。

```sh
evidence_dir=$(mktemp -d /var/tmp/omk-field-evidence.XXXXXX)
since='2026-10-23T23:50:00+09:00'
until='2026-10-24T00:30:00+09:00'
date --iso-8601=seconds > "$evidence_dir/captured-at.txt"
docker compose ps --all > "$evidence_dir/compose-ps.txt"
docker inspect --format '{{json .State}} {{.RestartCount}} {{json .HostConfig.LogConfig}} {{.LogPath}}' \
  omk-mosquitto omk-sensor-collector > "$evidence_dir/container-state-log-config.txt"
docker logs --timestamps --since "$since" --until "$until" omk-mosquitto \
  > "$evidence_dir/mosquitto.log" 2>&1
docker logs --timestamps --since "$since" --until "$until" omk-sensor-collector \
  > "$evidence_dir/collector.log" 2>&1
systemctl show omk-mqtt-ap-proxy.socket omk-mqtt-ap-proxy.service \
  -p ActiveState -p SubState -p ActiveEnterTimestamp -p NRestarts \
  > "$evidence_dir/proxy-state.txt"
journalctl --since "$since" --until "$until" -o short-iso-precise \
  -u omk-mqtt-ap-proxy.socket -u omk-mqtt-ap-proxy.service \
  -u omk-ap-activation.service -u NetworkManager -u docker -u systemd-journald \
  > "$evidence_dir/gateway-journal.log"
journalctl --disk-usage > "$evidence_dir/journal-usage.txt"
journalctl --list-boots > "$evidence_dir/journal-boots.txt"
systemd-analyze cat-config systemd/journald.conf > "$evidence_dir/journal-config.txt"
cp -- data/sensors/2026/10/23.jsonl "$evidence_dir/23.jsonl"
cp -- data/sensors/2026/10/24.jsonl "$evidence_dir/24.jsonl"
python3 firmware/esp32/omk-node/scripts/analyze_diagnostics.py \
  "$evidence_dir/23.jsonl" "$evidence_dir/24.jsonl" --node <12桁node_id> \
  --since "$since" --until "$until" > "$evidence_dir/node-analysis.jsonl"
python3 firmware/esp32/omk-node/scripts/analyze_diagnostics.py \
  "$evidence_dir/23.jsonl" "$evidence_dir/24.jsonl" \
  --since "$since" --until "$until" > "$evidence_dir/all-nodes-analysis.jsonl"
(cd "$evidence_dir" && sha256sum -- *.jsonl *.log *.txt > SHA256SUMS)
```

必要な権限で読む。既存データは変更しない。記録中ファイルのコピーには途中行が
含まれ得るのでinput_errorsを確認し、試験終了後に別の新規退避先へ再コピーする。
古い退避は上書きしない。Dockerのlogging driver、max-size/max-file、journaldの
Storage/MaxRetentionSec/SystemMaxUseと残存bootを確認する。空のLogConfigは
daemon既定を使い得るため `/etc/docker/daemon.json` と `docker info` も確認する。
設定の上限は残存を保証しない。開始時刻のログが実際に読めるか確認し、ローテート前に
退避する。サービス再起動、設定変更、journal vacuumはこの手順に含めない。
Mosquittoの既定ログは接続/切断用で、各PUBACKを記録するものではない。

解析スクリプトは入力を読み取るだけで標準出力へJSONLを出す。全入力を時刻順に
統合し、registration status/ackのNode↔logical ID候補、SEN66受信間隔、同じ
sensor boot内のread/enqueue差分、status、mesh recovery、各診断のpayloadを
元ファイル/行番号付きで返す。mappingは歴史上の候補であり、登録変更をまたぐ
所有関係の断定には使わない。窓外のregistrationを含むファイルも入力できる。
`--logical-id` でも選択できる。窓内の全Node/topic受信数と最終受信時刻をsummaryに
残す。同時刻の欠測はフィルタなしの出力でも比較する。

`accepted_but_not_in_log = enqueue差分 - 1` は受付後にJSONLで見えない数、
`reads_not_accepted_between = read差分 - enqueue差分` は受付以前の差。
Node/無線/Broker/collectorのどこで失ったかをこれだけで断定しない。
再起動では差分を切り、32bit連番wrapを扱い、逆転・不明な連番は不確実と表示する。
statusはboot_count変化またはuptime減少でカウンタ差分を切る。
view=1/2は履歴として扱い、同じNode/view/boot/seq/at_us/ELFの再送をまとめる。
`matches_latest_live_boot` は入力窓の最後のview=0との比較で、受信時に発生した
障害という意味ではない。view=0がなければ判定不能。retain付き受信は新たな
状態遷移と解釈しない。

collector停止中のQoS 0は後から再生されない。全Nodeが同時に消える場合は
Broker/collector再起動、AP proxy、ディスク書込エラーを先に照合する。
1台だけでもNode障害とは断定せず、USBの読取り・受付・ACK進捗と合わせる。

## 検証範囲と残る限界

ホストテストは実際のRAM getter/USB formatter、最大値、SEN66なし、mutex busyと
容量不足の区別、queue破棄の重複防止、backoff、保存予算と同種再発、日付跨ぎ/
再起動/過去view再送の解析を検証する。USBテストはMQTT/I²C実装をリンクせず
成立する。既存のmutex占有統合テストとUSB互換テストも維持する。
Flash/DRAM差分・テストログ・最終build・ソース・設定・チェックサムは新しい
成果物保存先に保全する。実機での原因識別能力を証明したものではない。

追加常駐RAMは6×uint32の24バイトと4フレームの計数済みflag 4バイト。
RTCレコードは同じサイズ。CPU負荷は該当拒否時の短い既存lock下の加算、
既存observer内の最大8×8 token比較、要求時のコピー/整形。
USB取得用のheap確保はない。通常statusの既存確保枠は増やさない。
CPU実時間、stack余裕、無線負荷は実機未測定。

CPU/USB/observer全停止、電源断前の未保存記録、QoS 0の受付後の消失、
SDK内部の具体的待機原因、collector停止期間の個々の測定は依然復元できない。
可能なら停止中の2回のUSB採取とGateway原ログを先に保全する。
今回の整備はここで完了とし、その証拠で必要性が判明するまで監視機構を増やさない。
