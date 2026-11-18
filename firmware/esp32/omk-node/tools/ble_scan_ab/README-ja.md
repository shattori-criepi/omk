# BLEスキャンA/B計測版のホストツール

このディレクトリのツールは、保存Native Mesh v2（基準commit `ff1eb4e`）のtopic／BLE decoderを使用する。BLE Sensor Managerのcallback、BlueZのスキャン設定、稼働サービスは変更しない。現場Gatewayの配置版・SHA・ACLは未確認。今回、実機接続や以下の収集／配布コマンドは実行していない。

## 実行環境と日時

Python **3.10以上**を対象とし、今回の実行検証は **3.14.4**。ローカルにPython 3.10実行環境が見つからず、3.10での実行は未検証。`+0900`／`Z+0900`を検証した後、`+09:00`へ明示的に正規化して`datetime.fromisoformat()`へ渡す。負offsetも同様で、UTC変換・異常値拒否を維持する。テストは渡される文字列そのものを照合し、3.10互換の文法も確認する。標準ライブラリ中心で、qualityは保存v2のBLE decoderを参照する。日時は明示的なtimezoneを必須とし、`2026-11-17T09:00:00.123456Z+0900`、`...123456+0900`、`...123456Z`、`...123456+09:00`、有限数値のUNIX秒、sensor-collectorの`received_at`に対応する。`Z+0900`はMosquitto旧版の書式として数値offsetを採用する。timezoneなし、不正日付・offset、NaN等を推測で補わない。recordsはinvalid行として報告し、計算から除外する。時刻文字列を数値と推測しない。

[2.0.11の公式実装](https://github.com/eclipse-mosquitto/mosquitto/blob/v2.0.11/client/sub_client_output.c#L244)と[2.0.18の公式実装](https://github.com/eclipse-mosquitto/mosquitto/blob/v2.0.18/client/sub_client_output.c#L234)の出力書式に従うfixtureを`tests/fixtures/ble_scan_ab/`に保存した。時刻・メッセージは合成であり、現場Gatewayからの実採取ではない。導入版の実出力1行との照合は導入前に残る。

## オフライン操作

`python3 ab.py decode payload.json` はkind 8の共通JSONを検証し、256Bレコードを展開する。バイト順はlittle-endian。version=1、4 bank各14×uint24、uint16の計測専用統計を使う。飽和したbankを差分率に使わない。flags／GAP／plan_stateの数値は `src/ble_scan_trial.h` と `ble_scan_metrics.h` を参照。

`python3 ab.py compare mqtt.jsonl --nodes ID1 ID2 ID3 ID4 > comparison.json` はmosquitto `%j` JSONLを読み、重複、retained、遅配、boot変更を区別する。実適用、epoch、区間、Root MAC、親切断件数、Layerが整合する連続report間だけを条件比較へ入れる。150秒超のgapや条件切替を跨ぐ区間は条件不明。さらに90秒超のreport欠測は下記の停止前兆分類でunknownとする。全4台の同時観測が揃ったものをnetwork、残りをpartialとする。`boot_cumulative_exploratory` は復帰後に回収した停止期間を含む累積であり、既知条件区間とは別物。Node・UTC日付・Root・条件で層別したヒストグラムと停止期間外TIMEOUT率を出す。UTC日付は実験夜の境界と一致しない場合があるため、最終解析では現地の夜・予定区間へまとめ直す。独立試行を仮定するp値や改善判定は出さない。

SDKの5ビンは `<20ms`, `20–<200ms`, `200–<2000ms`, `2000–<5000ms`, `>=5000ms`。全SDK完了を含み、無線上のホップ別ACK待ち時間ではない。停止期間外TIMEOUT率の分母は専用 `outside_completions`。停止中・5分の復帰確認待ち・境界を跨ぐ呼出しを分母から除外する。新規停止発症率の分母はreportだけから正確に復元できないので、自動推定しない。計画表、既存診断、欠測・介入記録から有効な発症リスク時間を確定する。

BLE/SEN66は `python3 quality.py mqtt.jsonl --models models.json --paths ID1 ID2 ID3 gateway_hci --hci hci-normalized.jsonl --manual manual.csv` で集計する。`--hci` と `--manual` は省略可能。models.jsonは確認済み個体MAC（小文字12桁）→ `motion_sensor` / `contact_sensor` / `presence_sensor` の対応とする。既存decoderを純粋関数として呼び、メーカー名だけから型番を推測しない。SEN66の重複除外後到達数・受信間隔・60秒超gap、BLEの経路別状態変化を出す。

自然イベントは初回状態を辺として数えず、5秒以内の同じ状態変化を観測基準へ束ねる。短い往復／矛盾する状態のclusterはambiguousとして比率から除く。全経路共通の見逃しは見えない。HCIをRF上の全広告、またはBleak callbackと同じ母集団にしない。出力の観測基準数は真の発生数ではない。B候補を短時間の少数成功だけで採用しない。

manual.csvの列は `timestamp,address,field,value,trial`。timestampはtimezone付きISO8601、fieldはmotion_state/contact_state、valueは0/1。物理操作を別途記録し、次の操作または30秒までの状態変化と対応させる。遅延はセンサー内部の検出・保持時間を含む。短い往復で対応不能ならrawを見直し、検出成功に補完しない。A/B比較はcomparison.jsonで確認した同じ配置・Root・条件区間にログを分割して行い、分割境界の最初の状態が除外されることを記録する。自然イベントと手動操作を混ぜた単一検出率は出さない。

## 停止前兆と欠測の分類

FWの停止判定、boot累積、Flash schema 2は変更していない。解析は全ログを先に読み、同じNode/bootの`episode.onset_s`（需要あり・成功なし180秒を経た停止確認時刻）を収集する。後続reportで初めて判明した停止も、過去の半開区間`[onset-180秒, onset)`へ遡って反映する。受信時刻ではなくNode uptimeで境界を決める。負の下限は0へ切り上げる。

- `stable_observed`：条件が既知で、前兆・停止・復帰確認待ち・不明範囲のいずれにも重ならないreport差分。
- `precursor_or_boundary`：前兆180秒と少しでも重なる差分。境界を跨ぐ累積値を時間比で分割しない。停止確認を含む境界でもこのラベルとし、率にはFWの停止期間外カウンタだけを使う。
- `stopped_or_recovering`：前兆ではなく、両端いずれかの停止／5分復帰待ちflagが立つ差分。
- `unknown`：90秒超のreport間隔、停止件数が一度に2以上増加して途中のonsetを復元できない範囲、またはboot観測末尾。欠測開始の直前180秒も不明とする。末尾180秒を右打切りとし、末尾側で確認済みのonsetがあればその時刻以降を不明とする。不明は他分類に優先する。停止未確認の通信消失を健全や発症とみなさない。

`groups`はNode・UTC日付・Root・preset・network/partial・phase別。`timeout_comparisons`は同じ層で、`stable`と`including_precursor`の分子`outside_TIMEOUT`／分母`outside_completions`と率を別々に出す。前者はstableのみ、後者はstable＋precursor_or_boundary。不明と停止／復帰待ちだけの区間を除外する。SDKヒストグラムは全SDK完了を含むため、前兆境界に停止後の呼出しが混在し得る。phaseを混ぜず評価する。180秒は今回の解析規則であり物理的な因果境界ではない。

`incidence_rate`は自動算出せずnullとする。新規エピソード数はboot累積の差分を記録し、停止前から継続中の事例・boot変更・介入・脱落を別表にする。有効な需要あり非停止時間を既存診断と運用記録で確定してから発症率を算出する。8〜10夜だけで十分な検出力があるとは扱わない。

## 条件別BLE品質の集計手順

1. 原ログは保全し、compareの`intervals`から実適用・Root・bootが既知の連続区間を抽出する。予定したpresetだけで分類せず、切替・GAP適用待ち・unknown・停止・脱落を明示する。networkとpartialは別集計にする。
2. 区間表にUTC開始/終了、各Node/boot/実preset、Root、phase、除外理由、ログSHAを保存する。同一配置と機種でA/Bを対応付け、層が異なる区間を黙って結合しない。
3. 表の各半開区間でMQTT JSONL、正規化HCI、manual.csvを同じUTC境界に切り出し、`quality.py`を区間ごとに実行する。HCI/MQTTの時計ずれを確認する。手動イベントは判定窓（最大30秒）が区間内に収まるものだけ採用し、境界を跨ぐものは別表へ置く。切出し先・コマンド・結果を区間表に紐付ける。
4. 自然変化の観測和集合、手動操作の取りこぼし・遅延、受信空白、SEN66をそれぞれ比較する。初回状態除外・曖昧な往復・全経路共通見逃しを記載する。停止Nodeを分母から黙って除いて改善と判定しない。

Rootの`mesh_rx_ok`にはkind 8も含まれる。旧Root受信数と子の通常SDK成功数の単純差は中継損失ではない。送信要求→子SDK→Root wire受信→MQTT→Gateway保存の各段で、kind、方向、Node/boot、期間、重複、drop、QoSの計数範囲を合わせる。kind別補正や到達点の対応が取れない集計は参考値に留め、SDK成功をGateway保存到達と混同しない。

## 有限予定の作成・配布・取消（導入承認後のみ）

1. 4台を同一版へ更新し、Aで最低1夜（可能なら2夜）観測する。30分の初期確認はスモークテストとする。
2. 新着measurementとstatusから現Root、対象非Rootのboot ID、uptime、最大epoch、実適用Aを確認する。物理IDはハッシュIDで、Root MACの文字列表現ではない。
3. 例：`python3 ab.py plan --log mqtt.jsonl --node CHILD_ID --root ROOT_ID --epoch 1 --presets A B40 A B40 --seconds 3600 3600 3600 3600 --out plan-child.json`。4区間以内・全体10時間以内。既定150秒後開始、受付期限は作成時推定Node uptime+90秒。同じ全体開始時刻に揃える際は各Nodeの新着uptimeを換算し、作成時差を記録する。各Nodeの予定を作成後、境界の実適用を照合する。完全同期を仮定しない。
4. ファイルを確認して `python3 ab.py send plan-child.json --log mqtt.jsonl --broker localhost`。このサブコマンドだけがMQTT publishを行う。QoS1、非retained、同一bytesで最大6回、5秒間隔。Root nonce／Node boot／期限が変われば停止する。PUBACK・SDK成功はNode適用やGateway保存のACKではない。新着measurementのepoch・結果・実presetを確認する。未確認は成功扱いにしない。送信前から消費済みのepochは別の予定ファイルと区別できないため成功判定せず終了する。複数CLIで同じNode/boot/epochを同時操作しない。ACKはこの配布後の新着reportでnonce・終端・plan_state・GAP実状態・実presetも照合する。planはACTIVE、GAPはRUNNING、desired=effective=report uptimeで期待するpresetが必要。開始前のA一致は受理確認でありBの適用確認ではない。取消はFINISHEDかつRUNNING/Aが必要。適用待ち・退避・FAILED/UNKNOWNは成功としない。未確認で終了しても自動的にepochを更新せず、次のreportを確認する。
5. 取消は `python3 ab.py cancel ... --epoch 2 --out cancel-child.json` で新epochを作り、同じsendを使う。通信断なら取消到達を保証できない。Nodeは既に受理した有限期限に従い、切断中Aへ退避、同Root再確認後5秒で現在区間へ再開し、最後はAへ戻る。旧epochの再配送で期限を延ばさない。

未達のBへ新nonceを付け直す自動再配布は行わない。新しい操作としてboot・Root・期限を確認して作り直す。Rootにはスキャン予定を受理させない。コマンドは認証の代替ではなく、閉域Mesh／MQTT ACLを前提とした古い配送の排除である。4台一括の原子的適用はなく、部分配布はpartialとして扱う。

## 収集（導入承認後のみ）

第一選択は `btmon` と `mosquitto_sub` の別プロセスによる収集。新しいBleakScannerを起動する既存raw_scan.pyは使わない。まず導入版の `btmon --help` / `mosquitto_sub --help`、権限、adapter index、時刻同期、空き容量を確認する。btmonが権限不足なら処理は失敗して終了し、自動sudoやサービス変更を行わない。

`python3 collect.py --out capture-UNIQUE --adapter 0 --seconds 1800 --max-bytes 536870912` で30分の負荷・容量確認を行う。既存MQTTの `omk-relay/+/ble/raw`, `omk-relay/+/sen66/raw`, `omk/node/+/status`, `omk/node/+/measurement` を購読し、`mosquitto_sub -F '%j'` で原payloadをエスケープ保存する。`%J` と異なり不正JSONも生ログに残る（[Mosquitto公式仕様](https://mosquitto.org/man/mosquitto_sub-1.html)を確認）。両MQTT CLIは空の一時XDG設定ディレクトリを使い、利用者の既定configにあるretain／retained消去等のオプションを継承しない。認証が必要な環境の接続情報は導入前に明示的に調整する。HCIは `btmon -i 0 -w ...` でbinary保存し、終了後 `btmon -r hci.btsnoop -T` で検査する。プロセス終了、予算到来、空き容量256MiB未満で両方を止め、capture.jsonに理由と時間を残す。サイズは1秒監視であり、監視待ちと子プロセス終了待ちの流量分だけ上限を超え得る。容量にはその余裕も含める。

`--seconds`と`--max-bytes`は省略不可。予定10時間の場合は`--seconds 36000`とし、受付前後の余裕を含めて収集時間内に予定を収める。収集が途中終了した場合は欠測を記録し、有限予定の終了までは待つ。30分の実測bytes/秒から夜の必要容量を見積もり、余裕を取り、最大10時間・未使用ディレクトリへ明示的に1回ずつ収集する。無期限常駐・サービス登録・自動削除・自動上書きはしない。btmon.stdoutも容量に含む。終了時にSHA256を取り、保存先を別媒体へ複製する。途中disconnectとloggerの再接続空白はstderr、report seq、capture開始終了時刻で確認する。欠測を広告未受信やNode停止と決めつけない。

夜間収集は、導入承認後に**tmux等の管理されたセッション内**で実行する。例：`tmux new -s omk-ble-ab`を開き、上記の期間・容量指定付きcollectコマンドを実行する。SSHから離れる際はtmuxをdetachし、再接続後は`tmux attach -t omk-ble-ab`で確認する。終了操作はcollectorへのCtrl-Cまたは対象PIDへのSIGTERMとし、`capture.json`と子プロセス回収結果を確認してからセッションを閉じる。tmuxの自動導入、常駐サービス登録、Gateway設定変更は行わない。管理されたセッションの利用は収集容量・終了期限の代わりにならない。

SIGHUP／SIGTERM／SIGINTは最初の停止要求を記録し、通常のfinallyで直接起動した2プロセスを回収する。handlerから例外を投げないため、Popenが戻って子handleを登録する間や、cleanup中の再通知で回収が中断されない。通常期限、容量上限、空き容量不足、子の先行終了、起動途中や監視処理の例外でも、既に起動した子と開いたfileを順に処理する。

子ごとにSIGINT→最大5秒wait、必要ならSIGTERM→最大2秒wait、最後にSIGKILL→最大2秒waitとする。Pythonが進行できれば直接子2台のwaitは合計最大約18秒（I/O等は別）。片方の失敗でも残りの回収を試みる。`capture.json`の`signal`、`children`（command/PID、signals、wait_timeouts、reaped、exit_code、forced、errors）、`cleanup_ok`、`cleanup_errors`を確認する。reapedはwaitで終了を回収できたことを表し、ログ末尾の完全性を保証しない。SIGKILLが必要だった場合、回収不能、close／記録失敗では終了コード2とし、成功扱いにしない。capture.jsonが書けなければ同じ結果をstderrへ出すため、tmuxの出力も確認する。

**保証できない範囲**：親PythonのSIGKILL、OOMによる強制終了、interpreterの突然死、OS／電源断ではfinallyも実行できず、子の孤立や終了記録欠落があり得る。カーネル内で終了不能な子（例：割込み不能I/O）はSIGKILL後も残り得る。Python自体やファイルI/Oが停止した場合もcleanup所要時間を保証できない。親が死んだことだけで子も停止したと判断しない。対象は直接起動したbtmon／mosquitto_subであり、別のwrapperが起動・daemon化した子孫の管理まで保証しない。異常後は収集セッション／PIDとログを確認して個別に終了判断し、欠測・末尾破損を記録する。自動再起動・監視サービスは追加しない。

btmonの標準保存は対象MACだけのフィルタではない。対象adapterだけを選び、MAC/address type/service/manufacturerは保存後に絞る。RFのscan filterを変更しない。HCI binaryの独自parserは導入版未確認のため追加しない。対象イベントをbtmonのデコード表示と照合し、必要な比較区間を次の正規化JSONLへ抽出する（現在は版を確認した手動工程）。

`{"source":"hci","timestamp":"2026-11-17T21:00:00.123+09:00","address":"aa:bb:cc:dd:ee:ff","rssi":-60,"manufacturer_data":{"0969":"..."},"service_data":{"0000fd3d-0000-1000-8000-00805f9b34fb":"..."}}`

manufacturer_dataのhexにはcompany ID自体を含めず、service_dataにもUUID自体を含めない。広告とscan responseの合成／重複抑制を勝手に行わず、元のHCI時刻・controller・address typeとの対応表も保存する。正規化が未検証の区間は直接受信比較を保留する。Gatewayサービス変更案より運用上の抽出負担が残るが、受信挙動への介入を避ける。

## 導入前の必須確認

Gatewayの実配置SHAとv2 topic/decoder、対象3機種の個体・FW・広告周期、Root/Node ID、MQTT ACL、logger時計・権限・版、容量・CPU影響は未確認。CPU/RF共存、実GAP停止時間、stack high-water、実heapはホスト試験では保証できない。SDK閉ソース内部の待ちの内訳も未観測。

4台のUSB作業は各1回へまとめる。各台で旧Flash 8192Bと取得可能なliveを回収・二重保全し、Node IDと共通BIN SHAを照合、app-only書込み、新版起動、必要な再arm、全8192BのFFとreadyを確認してからBLE/Mesh/SEN66を確認し、元の場所へ戻す。階段下に新規記録があるとは仮定しない。blank/partialも保全する。旧保存ソース・BIN・ELF・診断ツールは変更せず、コピーした旧decoderを使う。新旧FWを混在した一晩試験は行わない。詳細は設計書第9節を使う。

診断event ringには新type=8を使い、`code` の32bitパターンがcontrol epoch、`detail` のbit 0..2が要求preset、3..5が実preset、8..15が結果を表す。旧decoderは数値として保存できる。10秒以上間隔で最新1件を記録し、定期report自体はeventを作らない。16件ringの既存障害履歴を押し出す可能性は残る。Flash schema／保存trigger／回数は変えない。

## 今回の運用制約

通常pacingがactiveの間、senderでkind 8をSDK未実行dropへ計上する。通常データだけが従来のタイミングで抑制解除を試みる。dropとSDK失敗は二重計上しない。復帰後の同boot累積で回収可能だが、失われた途中reportの詳細時刻・topology・BLE実適用は復元できない。共有queueへの投入、取り出し、CPU時間の負荷は残り、queue満杯による通常データへの影響を消すものではない。

臨時reportはコマンド受理／拒否／取消、予定の開始／終了／退避／再開、GAPの完了／失敗／timeoutを契機にし、topologyだけの更新では発行しない。臨時reportは直近reportから10秒以上（最大6回/分）。別の1分周期reportが直前の臨時reportから10秒未満で出る場合はあるため、全reportについての厳密な最短間隔10秒とは主張しない。状態の30秒周期は独立して維持し、topologyは既存statusと周期reportで照合する。

GAPのfailedラッチは役割変更で解除しない。FAILED/UNKNOWNでAへの復帰を確認できない場合は、そのNodeの試験を中止する。追加コマンドや役割変更でGAP APIを繰り返して復旧させず、手動復旧の必要性をその都度判断する。Root昇格に伴う停止確認だけでスキャン健全性を保証しない。再起動の作業負担とSEN66 VOC/NOxの学習・安定待ちへの影響、時刻、boot変更を記録する。他Nodeはpartialとして継続可能だが全台同条件の試験とは分ける。
