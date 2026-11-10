OMK Native Mesh P1 — 固定した試験版
2026-11-10 / experiment/native-mesh-p1-20261110

P1修正・差分レビュー・AtomS3 Lite build・オフライン検証を終えた実機試験候補。
実機未検証。この保存作業では実装変更、追加テスト、再ビルド、Gateway変更、書込み、pushは行っていない。
このファイル、manifest.json、source-files.sha256.json、配布物を含むGit commitを一組の版とする。

1. 正式な保存対象

- firmware/esp32/omk-node/: P1修正後のNative Meshソース・build設定・host tests。
- services/ble-sensor-manager/: ID予約と所有者/revision照合、有限MQTT再送、既存BLE decoderとtests。
- services/sensor-collector/: Native metadataの扱いとtests。
- scripts/tests/とUSB helper: P1確認時と同じ既存ソースを基点から保持。
- docs/decisions/esp-wifi-mesh-node-networking.md、docs/developer/data-and-mqtt.md、
  docs/developer/native-mesh-migration.md: 設計と移行条件。
- atom-s3-lite-app-only/: 配布準備済みfirmware.binと対応firmware.elf。BINはappだけ。
- distribution/: 配布準備済みGateway実行ソースtarと既存USB helper tar。
- reference-only/: 同じbuildのbootloader/table。通常更新では書かない。
- build-inputs/: 実際のAtomS3 buildに使ったlock、実効sdkconfig、入力ソースのSHA一覧。
- validation/: P1検証時の既存ログと、公開用のパス正規化前後のSHA。
- HARDWARE-TRIAL-ja.txt: 実機IDを除いた移行・USB識別・3台Layer 3試験・復帰手順。

ローカルorigin/mainのc651e66eb29bfce7b0a68d51a5848208d4427479を基点に、
P1保存snapshotとSHA照合した範囲を取り込んだ。既存の別作業の未公開commit履歴は取り込まない。
旧IP/NAT通信・診断patch等の削除はNative実装済みsnapshotに従ったもの。
独立コピーに残っていた過去の個体別診断資料、partitions.coredump.csv、古いprebuilt、
旧版のLICENSE_AUDITは取り込まない。公開基点のprebuilt・監査資料・release資料はそのまま保持する。
Git保存のため編集したのは文書と来歴資料。実装・テストコードはP1保存時と同じ。

重要: firmware/esp32/omk-node/prebuilt/ は旧IP/NAT版。今回のNative配布物ではない。
Dashboard初期化や既存package scriptでこれを今回の試験版として書き込まない。
本試験BINは表示app versionが「1」であり、Git SHAを内蔵していない。
BIN/ELFを変更して版を埋め込むことはせず、完全SHA-256と保存commitで追跡する。
ELFは元のdebug情報・compiler/SDK build pathを含む。stripしていない。

2. 対応commitと成果物の確認

repo rootで実行（読み取りのみ）:
  git log -1 --format=%H -- experiments/native-mesh-p1-20261110/manifest.json
  cd experiments/native-mesh-p1-20261110
  sha256sum -c SHA256SUMS

BIN SHA-256:
  a906ce5fd7e833ba12e6be47fa0b5b073cadc8a8047a28518988e758ce88f211
ELF SHA-256（BIN内app descriptorのELF SHAとも一致）:
  0afe4bfeb10319b93917a3d8a7b600f8d22208afcbfc8ce928c4382e2b40c530

app offset=0x10000、ESP32-S3 / AtomS3 Lite、ESP-IDF 6.0.1、BLE ON。
BINとELFはP1 build出力と配布準備コピーの両方に全byte一致。
Gateway/USB tar内の全ファイルもこのcommitの対応ソースに一致する。
Node別の非公開試験台帳に「Node ID、保存commit、BIN SHA、ELF SHA、書込み日時、
USB識別、readback/verify結果、個体の旧app/NVS復帰先」を記録する。
Gatewayには「保存commit、tar SHA、実効service/venv/image ID」を記録する。
将来ソースを変更した場合はこのreleaseのBINを上書きせず、新しい版と成果物を作る。

3. 再buildの入力と手順（今回未実行）

PlatformIO Core 6.2.0、espressif32@7.0.1、framework-espidf@4.60001.0 (IDF 6.0.1)、
Xtensa GCC 15.2.0+20251204。ESP-MQTT 1.1.0（無patch）、cJSON 1.7.19~2。
その他のtool package版はvalidation/build-atom-s3-lite.logのPACKAGES欄。
元buildはGit管理外の隔離ディレクトリで作ったためapp versionは1となっている。
source-files.sha256.jsonはP1 snapshotと一致する438件のソース・テスト・共通入力を記録する。
build-inputs/source-files.sha256.jsonの43件は、元の隔離buildディレクトリとも一致確認済み。
元sourceのdependencies.lockのtargetはesp32。AtomS3 build時のlockはtargetだけesp32s3で、
依存版/hashは同じ。対応lockをbuild-inputs/dependencies.esp32s3.lockに保存した。

別の一時ディレクトリへcommitのfirmwareだけを展開し、buildする例:
  OMK_TRIAL_COMMIT=$(git log -1 --format=%H -- experiments/native-mesh-p1-20261110/manifest.json)
  OMK_REBUILD_DIR=$(mktemp -d)
  git archive "$OMK_TRIAL_COMMIT" firmware/esp32/omk-node | tar -x -C "$OMK_REBUILD_DIR"
  git show "$OMK_TRIAL_COMMIT:experiments/native-mesh-p1-20261110/build-inputs/dependencies.esp32s3.lock" \
    > "$OMK_REBUILD_DIR/firmware/esp32/omk-node/dependencies.lock"
  python3 -m venv "$OMK_REBUILD_DIR/pio-venv"
  "$OMK_REBUILD_DIR/pio-venv/bin/pip" install platformio==6.2.0
  PLATFORMIO_CORE_DIR="$OMK_REBUILD_DIR/pio-core" \
    "$OMK_REBUILD_DIR/pio-venv/bin/pio" run -d "$OMK_REBUILD_DIR/firmware/esp32/omk-node" -e atom-s3-lite

依存取得にはネットワークが必要。後日のregistry/host環境を固定するものではない。
保存済みBINのbyte再現性は主張しない。日時、絶対build path、tool package解決等でhashは変わり得る。
試験には保存済みBIN/ELFを使い、再build品を同じhashの成果物として扱わない。
SDK/vendor cacheやvenvをGitへ混入させず、設定・依存版・入力・元のbuildログから再build可能にする。

4. 検証済みの範囲と再実行方法（今回未実行）

2026-11-10 P1修正後: Firmware + BLE Manager 491、Collector 11、scripts 234 = 736件成功。
USB関連4 suiteの132件はscriptsの234件に含む。二重計上しない。
既存fixture 2件は未完了: test_host_mqtt_persistence.py / test_final_review_mutations.py。
収集/fixture準備が既存B-routeのsudo処理に到達し、コンテナ制約で停止した記録を残す。
「全fixtureが成功」「一度も実行を試みていない」とは扱わない。
test_ap_socket_kernel.pyは実kernel/network namespaceが必要でオフライン対象外。
AtomS3 Lite build SUCCESS、static RAM 72,880 bytes、flash 1,533,957 bytes。
P1差分再レビューで新規P0/P1なし。実機でのRF/BLE共存、多段転送、Root交代、長時間安定性は未確認。

後日の再実行は、保存commitの別checkoutで専用Python環境を作り、両サービスのrequirements、
scripts/testsが必要とするPyYAML・pyserial等とhost C compilerを用意する。
firmware build後のmanaged_components/espressif__cjson/cJSONをOMK_TEST_CJSON_DIRへ指定する。
repo rootからのコマンド例（依存環境を準備済みの場合）:
  export OMK_TEST_CJSON_DIR="$PWD/firmware/esp32/omk-node/managed_components/espressif__cjson/cJSON"
  PYTHONPATH="$PWD/services/ble-sensor-manager/src" python -m pytest -q \
    firmware/esp32/omk-node/tests services/ble-sensor-manager/tests
  PYTHONPATH="$PWD/services/sensor-collector/src" python -m pytest -q services/sensor-collector/tests
  python -m pytest -q scripts/tests \
    --ignore=scripts/tests/test_ap_socket_kernel.py \
    --ignore=scripts/tests/test_host_mqtt_persistence.py \
    --ignore=scripts/tests/test_final_review_mutations.py

5. 実機導入前提と秘密情報

公開版手順書のA/B/Cは役割名であり個体を特定しない。実機台帳はGit外の非公開保存先に置く。
当日識別、全旧Nodeの停止、Gatewayと各Nodeの整合したbackupを完了してから試験へ進む。
P1修正前Native Rootも混在不可。Rootが直接共通topicへ送る旧版は所有者照合を迂回する。
解除対象が不在の間は旧IDを予約する。未確認計測は破棄し、永続telemetry queueは増設していない。
失敗時は全試験NodeとGatewayを停止し、個体別旧app＋試験前NVSとGateway台帳を同じ時点へ戻す。
計測履歴は巻き戻さない。NVS・factory secret・PoP・Wi-Fiパスワード・Flash dump・Gateway実データ・
新たな実機台帳・所在地情報はこの保存物に含めない。既存公開fixtureの識別子は保持している。
コード/fixture内の模擬credentialは実機credentialではない。
旧ソースarchiveや実機inventoryをそのまま公開せず、履歴も公開済み基点から分離した。
