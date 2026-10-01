# Gateway USB Node setup

正式対象はAtomS3 Liteのみ。Gatewayは追跡対象のprebuilt packageを検証し、system-manager venvの`esptool==4.11.0`で書き込む。開発環境のespressif32 7.0.1 / tool-esptoolpy 2.41100.0の`esptool.__version__`も4.11.0であることを確認済み。

## 開発者のpackage更新手順

1. firmware sourceを変更する。
2. host testsと関連testsを実行する。
3. `pio run -d firmware/esp32/omk-node -e atom-s3-lite`を実行する。
4. sourceをcommitする。
5. リポジトリ内のclean HEADから`./scripts/build-omk-node-package.sh`を実行する。
6. `firmware/esp32/omk-node/prebuilt/atom-s3-lite/`の3 binaryとmanifestを確認し、source commit・version・SHA-256を照合する。[ライセンス監査の更新手順](../../firmware/esp32/omk-node/LICENSE_AUDIT.md)に従い、同じbuildのapp/bootloader情報からレビュー済み`sbom.spdx.json`を再生成し、配布binary・manifest・SBOMのhashを照合する。`LICENSE_AUDIT.md`の来歴も更新し、依存やリンク対象に差があれば第三者通知と再配布義務を再確認する。
7. packageを別commitにする。
8. pushする。
9. Gatewayでpullする。初回導入時は`scripts/setup-system-manager.sh`を再実行してvenv依存とdialout membershipを更新する。
10. Dashboardから対象Nodeをセットアップする。

生成scriptはGit状態取得の失敗を停止条件とし、untrackedを含むdirty treeを拒否し、build前後のHEADとclean状態を確認する。既存`.pio/`成果を再利用しないようPlatformIO cleanの後にbuildする。生成対象は3 binaryとmanifestのみで、SBOM・監査記録・第三者通知は自動更新しない。`source_commit`はbuild元の40桁SHAでありpackage commitではない。runtimeでGateway HEADとの一致は要求しない。`.pio/`やNode固有credentialは追跡しない。今回同梱したpackageのsourceはmanifestを参照する。

## 固定package仕様と境界

schema_version=1、target=atom-s3-lite、chip=esp32s3。segment配列は次の順序と名前、offsetで固定する。

| filename | offset | 最大サイズ |
|---|---|---|
| bootloader.bin | 0x00000000 | 0x8000 |
| partitions.bin | 0x00008000 | 0x1000 |
| firmware.bin | 0x00010000 | 0x200000 |

manifestの形式、regular file、symlink不使用、サイズ、SHA-256、segmentの過不足をUSBアクセス前に検証する。検証したbytesをprivate temporary directoryへsnapshotし、その固定ファイルだけを渡す。merged imageや全flash eraseは使用しない。NVS 0x9000とfactory secret 0xf000は通常書込みに含めない。

## 機器分類・identity・secret

探索対象はttyACMへ解決される既存allowlistのみ。ttyUSBへ解決されるby-idも除外する。OMK identifyに応答する既存Nodeは、Gateway credentialが欠けても新品にしない。OMK node_idとeFuse base MACからFNV-1aで算出したnode_idが一致する場合のみ通常書込みを行う。

identify未確認の場合はESP32-S3 chipと一意なMACを確認する。descriptorやOUIからAtomS3 Liteとは断定せず、実物確認を必須にする。同node_idのcredentialが存在すればrecovery_requiredとして停止する。明示確認した新品のみrandom 32-byte secretを生成し、OMKP + raw secretを0xf000へ書く。credentialは0600、directoryは0700。永続化とfsync後、factory書込みを試みる。書込み前のidentity失敗ではcredentialを削除し、試行後の失敗では保持する。既存Nodeではfactory secretとNVS prov_popを変更しない。

setup開始時に公開ttyACM名をallowlist内の優先パス（存在すればby-id）へ一度だけ解決し、以後その内部パスを保持する。initial MACを基準に各write boundaryで再確認する。書込み前のMAC検査とfactory書込み後は`--after no_reset`でbootloader内に留め、途中の再起動と次のserial openとの競合を避ける。factory書込み後、firmware書込み前にも再確認する。再列挙は選択した同一パスだけを待ち、別ttyを探索して追従しない。同じby-idで戻った場合もeFuse MACを確認する。MAC確認による再起動後、USB v2 identifyのnode_id一致を確認してからWi-Fiを送る。

## API・権限・完了条件

- `GET /api/nodes/usb-candidates`: 既存Node／未確認ESP32-S3／復旧必要をkindで分類。setup中はcacheのみ。公開deviceはttyACM名とし、MACを含み得るby-id文字列をAPI/UIへ出さない。
- `POST /api/nodes/usb-setup`: device、node_id、strict booleanのconfirm_atom_s3_liteだけを受け付け202を返す。追加fieldは拒否。
- `GET /api/nodes/usb-setup/status`: stage、node_id、安全なerror codeのみ。
- `POST /api/nodes/usb-provision`: 既存Wi-Fiのみの互換APIとして維持。標準UIはsetupを使用。
- `POST /api/nodes/usb-reinitialize`: 使用済みNodeの明示的な再セットアップ。旧PoP・Wi-Fi・Logical IDを置換/解除し、通常setupと同じlock・statusを使用する。[再試行とcredentialの扱い](../../services/system-manager/README.md#usb-node-setup)を参照する。

setupはoperation lockとserial access lockを既存provisioningと共有し同時に1件。setup中のcandidate pollingはcacheのみを返しportを開かない。idle時はserial lockの下で探索する。system-managerは既存非rootユーザーで動作し、setupでdialout groupを追加する。flash用sudo/helperや任意コマンドAPIは追加しない。ブラウザからはDashboard backendを通しBearer tokenを公開しない。

Wi-Fi送信後は対象node_idの新しいMQTT registration status（provisioned/registered）を待つ。setupではmosquitto_sub -Rで過去のretained statusだけによる誤成功を防ぐ。受信は現在のGatewayへの接続確認になるが、Mesh経路の全hop検査は行わない。タイムアウトは失敗とし、Wi-Fi設定済みだけでsetup完了に置き換えない。

PC用flash-omk-node.shは既存H3 guardを維持し開発・復旧用に残す。復旧ではcredentialを削除して新品扱いにせず、factory/NVSの状態と保存済みcredentialを確認する。

## 実機確認

新品・既存（Gateway credentialあり／なし）での完走、再列挙、USB差替え停止、工場secretの維持、電断後の復旧、AP変更後の再setup、SEN66登録、SEN66なしのBLE/Mesh、fresh Gatewayのdialout権限を確認する。自動testsはfake serial/esptoolを用い実USBを書き込まない。

以下は2026-09-11時点の実装・レビュー記録であり、テスト件数とcommit SHAは現在の配布物を示さない。現行packageの来歴は`manifest.json`とライセンス監査記録を参照する。

## 実装時の検証記録（2026-09-11）

- system-manager全tests: 129 passed。
- Dashboard全tests（USB UI、Node登録、proxy含む）: 123 passed。
- scriptsのpackage／flash／USB provisioning testsとomk-node firmware host tests: 187 passed。
- `bash scripts/tests/test_setup_omk_gateway.sh`、`bash scripts/tests/test_setup_ble_sensor_manager.sh`: 成功。
- `pio run -d firmware/esp32/omk-node -e atom-s3-lite`: 成功。clean HEADからpackage生成も成功。
- `git diff --check`、変更したshell scriptsの`bash -n`、admin.jsの`node --check`: 成功。

API testsはsandbox内でTestClientが停止したため、ローカル通信可能な実行環境で再検証した。Dashboardの既存Node.jsハーネスには新しいstatus APIのidle応答を追加した。実USBは使用せず、実機flash・commit・pushは行っていない。

## 変更ファイル一覧

- [docs/decisions/usb-node-setup.md](../../docs/decisions/usb-node-setup.md)
- [docs/user/dashboard.md](../../docs/user/dashboard.md)
- [docs/user/esp32-node-setup.md](../../docs/user/esp32-node-setup.md)
- [firmware/esp32/omk-node/README.md](../../firmware/esp32/omk-node/README.md)
- [firmware/esp32/omk-node/prebuilt/atom-s3-lite/bootloader.bin](../../firmware/esp32/omk-node/prebuilt/atom-s3-lite/bootloader.bin)
- [firmware/esp32/omk-node/prebuilt/atom-s3-lite/firmware.bin](../../firmware/esp32/omk-node/prebuilt/atom-s3-lite/firmware.bin)
- [firmware/esp32/omk-node/prebuilt/atom-s3-lite/manifest.json](../../firmware/esp32/omk-node/prebuilt/atom-s3-lite/manifest.json)
- [firmware/esp32/omk-node/prebuilt/atom-s3-lite/partitions.bin](../../firmware/esp32/omk-node/prebuilt/atom-s3-lite/partitions.bin)
- [scripts/build-omk-node-package.sh](../../scripts/build-omk-node-package.sh)
- [scripts/setup-system-manager.sh](../../scripts/setup-system-manager.sh)
- [scripts/tests/test_build_omk_node_package.py](../../scripts/tests/test_build_omk_node_package.py)
- [services/dashboard/README.md](../../services/dashboard/README.md)
- [services/dashboard/app/main.py](../../services/dashboard/app/main.py)
- [services/dashboard/app/static/admin.js](../../services/dashboard/app/static/admin.js)
- [services/dashboard/app/templates/admin_sensors.html](../../services/dashboard/app/templates/admin_sensors.html)
- [services/dashboard/tests/test_display.py](../../services/dashboard/tests/test_display.py)
- [services/dashboard/tests/test_switchbot_unrecognized.py](../../services/dashboard/tests/test_switchbot_unrecognized.py)
- [services/dashboard/tests/test_usb_node_provisioning_ui.py](../../services/dashboard/tests/test_usb_node_provisioning_ui.py)
- [services/system-manager/README.md](../../services/system-manager/README.md)
- [services/system-manager/requirements.txt](../../services/system-manager/requirements.txt)
- [services/system-manager/src/omk_system_manager/main.py](../../services/system-manager/src/omk_system_manager/main.py)
- [services/system-manager/src/omk_system_manager/node_provisioning.py](../../services/system-manager/src/omk_system_manager/node_provisioning.py)
- [services/system-manager/src/omk_system_manager/node_setup.py](../../services/system-manager/src/omk_system_manager/node_setup.py)
- [services/system-manager/tests/test_api.py](../../services/system-manager/tests/test_api.py)
- [services/system-manager/tests/test_node_setup.py](../../services/system-manager/tests/test_node_setup.py)

## commit前レビュー（2026-09-11）

26ファイル、実際のesptool argv、prebuilt partition tableとSHA-256を監査した。次の不整合のみ修正し、再現テストを追加した。

- Git status失敗の空出力をcleanとして扱っていた。build前後ともGit失敗時に停止する。
- by-id文字列に含まれるMACが候補API／DOMへ出得た。公開値をttyACM名に限定し、内部ではallowlistのby-idを保持する。
- setup POST失敗時に前回の完了statusを今回の成功として表示し得た。受付不明時は完了を表示しない。
- jobのfailure codeが未翻訳だった。固定の日本語説明に変換し、未知の文字列は表示しない。

unsupported chip／ambiguous MAC、hash不一致のwrite 0回、absolute path／missing binary／duplicate segment拒否、by-idの内部保持、固定subprocess argv、dialout追加順のテストも補完した。testのfactory secret比較は失敗出力にbytesを含めない形にした。

検証: system-manager 138 passed、Dashboard 125 passed、関連scripts／firmware host tests 189 passed。Gateway／BLE setup script tests、AtomS3 Lite PlatformIO build、bash -n、JS構文確認、git diff --checkも成功。prebuilt packageは変更せず、source_commitは2e961df52583260fbd1a69493bbdf2412169cdc1のまま。実機flash・commit・pushは行っていない。
