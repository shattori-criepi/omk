# Native Mesh V2D-P1（実験ブランチ）

FW: `native-BLE-AB1-V2D-P1`。対象は AtomS3 Lite / ESP32-S3、診断ON。
最終BIN SHA-256: `fa04fc425b5ebef9c8ca554234b6c7a1c88dd154bdf473aee66bb7b36af1e9eb`。

Native Meshの公開済み基点 `ff1eb4e` からB1、子機回復AB1–AB5、DIAG1/LR2R1、
BLE-AB1、V2、V2D、P1修正をまとめて取り込んだ。旧IP/MQTT Meshのmainや
lifecycle作業ブランチを基点にするとNative Meshへの移行を欠くため、この祖先を使用する。
実機導入済みBINとELFのハッシュ、BIN内のFW名・IDF版・ELFハッシュ、
ビルド時記録の137入力すべてを照合した。詳しい入力ハッシュと依存版は
`v2d-p1-provenance.json`。実装・ビルド設定・decoder/collectorは保存版と同一。
テストだけ旧作業ディレクトリ参照を修正し、実機IDを合成値に置換、非公開ログに
依存する比較テストを除外した。生ログ・過去レビュー・BIN/ELFは新規追加しない。
祖先commitの `prebuilt/` や `experiments/native-mesh-p1-20261110/` は旧版であり、
このFWの配布物ではない。

## ビルド

PlatformIO Core 6系、`espressif32@7.0.1`、ESP-IDF 6.0.1
(`framework-espidf@4.60001.0`)、Xtensa GCC `15.2.0+20251204` を使用。
各ビルドツールの正確な版はprovenance参照。cJSON `1.7.19~2`、MQTT `1.1.0` は
`dependencies.lock` に固定し、実ビルドに使ったコピーの全590ファイルが
registry配布時のCHECKSUMSと一致することを確認済み（ローカル修正なし）。
`src/probe_abi.cmake` は3つのESP Wi-Fi静的ライブラリをSHAで固定し、異なるSDKを拒否する。

通常の依存解決では `IDF_COMPONENT_MANAGER` を未設定にして、このディレクトリで
`pio run -e atom-s3-lite`。依存lockとSDK版を勝手に更新しないこと。
保存版と同じofflineビルドは、上記版のcJSON/MQTTを
`../offline-components/cjson` と `../offline-components/mqtt` に展開し、
`IDF_COMPONENT_MANAGER=0 pio run -e atom-s3-lite`。
既存の最終実験環境では、その `pio-core` を `PLATFORMIO_CORE_DIR` に指定できる。
これはビルドのみであり、uploadは実行しない。
`atom-s3-lite-diag-off` は比較用で、実機導入版ではない。他ボードやreset/wifi-set環境は今回の対象外。
ビルド日時・絶対パスを含むため別環境でのBINハッシュ完全再現は保証しない。
今回再ビルドはせず、保存入力と実物ハッシュによって対応を確認した。

## 診断ツールと主要テスト

`tools/ble_scan_ab/` にA/B collector・decoder・V2 probe・quality・replay、
`tools/legacy_diag/` にUSB collectorとFlash/payload decoderを置く。
`../tools/` は同じ実装への相対symlinkで、従来の呼び出しパスを維持する。
USB collectorはPython 3.10以上とpyserial 3.5、オフラインテストはpytestとCコンパイラを使用。
A/B収集にはmosquitto_sub/btmonが必要。操作は各ツールの `--help` と
`tools/ble_scan_ab/README-ja.md` を参照。採取ログは公開Gitへ追加しない。

リポジトリのルートから、ローカルに準備したcJSONの場所を設定して実行する。

```sh
export OMK_TEST_CJSON_DIR="$PWD/firmware/esp32/omk-node/managed_components/espressif__cjson/cJSON"
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q -p no:cacheprovider \
  firmware/esp32/omk-node/tests/test_diag_core.py \
  firmware/esp32/omk-node/tests/test_diag_runtime.py \
  firmware/esp32/omk-node/tests/test_diag_store.py \
  firmware/esp32/omk-node/tests/test_diag_usb.py \
  firmware/esp32/omk-node/tests/test_native_mesh_events.py \
  firmware/esp32/omk-node/tests/test_v2_quality.py \
  firmware/esp32/omk-node/tests/test_v2_tools.py \
  firmware/esp32/omk-node/tests/test_ble_scan_tools.py \
  -k 'test_diag_core or test_diag_runtime or test_diag_store or test_diag_usb or test_v2_quality or test_v2_tools or test_ble_scan_tools or (test_native_transport_events and (scan_m1 or ble_child_bridge or ble_roles or v2_gate_exclusion or sdk_upstream_contract or delivery_reconnect or tx_blocked_call))'
```

取り込み元で上記対象は386 passed / 1 skipped（legacy fixtureではprivate probe無効）。
全体再検証・実機操作・Gateway/MQTT/サービス設定変更は行っていない。
