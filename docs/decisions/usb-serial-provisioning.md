# USB Serial/JTAG Provisioningを正式採用

## 決定

Node初回登録の正式方式はUSB Serial/JTAG Provisioningとする。初回だけNodeをGatewayへUSB接続し、Gateway CLIがUSB serial候補を列挙してversioned `identify` JSON応答からNode IDを取得する。GatewayのOMK AP SSID/PSKを送信すると、Nodeは既存Wi-Fi credential storageへ保存してread-back照合し、software reboot後に通常のWi-Fi STA/MQTT registrationを開始する。AtomS3 LiteでMQTT registration statusまでのE2Eを確認済みである。

SSID、PSK、Node ID、物理ボタンの入力は不要である。USB抜き差しをreset手段として前提にせず、共通firmwareへcredentialだけを後から追加設定する。

現在の一般利用者向け初回導入は、[Gateway USB Node setup](usb-node-setup.md)で追加したDashboardからのprebuilt書込みとUSB provisioningを使用する。この文書のCLIは、通常firmwareが動作しているNodeへのWi-Fi設定・開発検証用として維持する。

## 不採用方式

- BLE GATT provisioning: Raspberry PiからAtomS3 Liteへの接続が不安定で、`le-connection-abort-by-local`、HCI `0x3e`が発生した。PPCP/MTU/PHY等の低レイヤ調査は停止し、保守コストを許容しない。
- BLEアドバタイズ設定用transport: Raspberry Pi kernel 6.18とBlueZ 5.82でLEアドバタイズ登録が`Invalid Parameters (0x0d)`となり、Gateway依存が強い。
- Temporary SoftAP + HTTP: Node側のWPA2 SoftAP、`/health`、`/config`、Node固有setup PSK、credential保存はPoC済み。ただし単一`wlan0`をOMK AP→STA→Node SoftAP→OMK APと切替えるGateway経路が不安定だった。
- ESP-NOW / Wi-Fi Aware等: Raspberry Pi側の特殊実装・driver依存・長期保守負担がUSB方式より大きい。

USB方式はLinux BLE stackやGateway Wi-Fi構成に依存せず、通常firmwareと通常のBLE sensor relayを維持できる。

## USB identity guard（protocol v2）

公開前監査H1/H2への対応として、USBだけprotocol v2へ更新する。BLE Discovery、MQTT、Node IDの生成方式はv1のまま維持する。上記E2E確認は旧実装の記録であり、以下の変更は実機再確認を必要とする。

- 自動探索は対応候補全体をidentifyし、realpathでまとめた物理portが0台なら失敗、1台なら自動選択、複数なら停止する。異なるportの同じnode_idをaliasとしてまとめない。
- `--device`はそのportをidentifyして選択する。`--node-id`だけなら全候補から一意に選択する。両方指定なら一致を要求する。`--clear-wifi`はどちらかの明示指定が必要。
- Dashboardはカードのnode_idとportをbackendへ送る。backendは再探索で照合し、最終操作にも同じnode_idを渡す。
- Pythonは操作用のserial接続を開いた後、v2 identifyと期待IDの照合を行い、同じ接続でのみ状態変更を送信する。不一致／timeout／旧protocolなら状態変更要求を送らない。
- `set_wifi`／`clear_wifi`の要求には`expected_node_id`が必須。firmwareは12桁小文字hexの自身のIDとの一致を確認してから保存／消去する。欠落・不一致なら`node_identity_changed`で拒否し、再起動も予約しない。v1状態変更要求は`invalid_request`。探索用identifyはv1も受け付け、返信はv2とする。
- USB処理の状態変更commandは上記2種類。accepted後の再起動も同じguardの後に予約する。開発用firmware書込みスクリプトは別経路で、今回変更しない。

旧firmwareへの自動fallbackは行わない。GatewayのCLI/system-managerとNode firmwareを両方更新し、system-managerを再起動する。通常firmware更新後にUSB設定を実行する。保存済みcredentialやlogical_idのデータ形式は変更しない。

この照合は偶発的な個体取り違えを防ぐためのもので、node_idを偽装する機器への暗号学的認証ではない。操作中に通信が切れた場合は別portへ自動で操作を転送せず、失敗として再選択する。

### 自動テスト

リポジトリルートから実行する。Python環境にはpytestを含む関連サービスの開発依存が必要。

```sh
OMK_TEST_CJSON_DIR="$PWD/firmware/esp32/omk-node/managed_components/espressif__cjson/cJSON" \
python -m pytest scripts/tests/test_provision_omk_node_via_usb.py scripts/tests/test_usb_node_provisioning_service.py firmware/esp32/omk-node/tests/test_usb_provisioning_protocol.py
```

firmwareテストは実際のCコマンドhandlerとcJSONをホストcompilerでコンパイルし、NVS相当の保存／消去呼出しをfakeへ置換して回数を検証する。`cc`とcJSON sourceが必要。現行ESP-IDF 6環境では`OMK_TEST_CJSON_DIR`に`firmware/esp32/omk-node/managed_components/espressif__cjson/cJSON/`の絶対パスを指定する。別配置でも同変数に`cJSON.c`と`cJSON.h`のあるディレクトリを指定する。依存がない場合はskipになるため、公開前確認ではこのテストが実行されたことを確認する。

### 実機再確認手順（未実施）

実機試験は人が行う。秘密情報・実在IDをfixtureや文書に転記しない。

| 条件 | 操作 | 期待結果 |
| --- | --- | --- |
| 1台・CLI自動 | 更新済みNodeを1台接続し、CLIを引数なしで実行 | 1台を選択し、設定・再起動・MQTT確認成功 |
| 2台・CLI自動 | 更新済みA/Bを同時接続し、CLIを引数なしで実行 | 曖昧エラー。どちらのWi-Fi設定も変わらない |
| 列挙順変更 | 2台の接続順・USB口を変えて同じ操作 | どちらの順でも曖昧エラー |
| alias | 1台のby-idとttyACMが両方存在する状態でCLI自動実行 | 2台扱いせず1台として成功 |
| Dashboard選択 | A/BのカードからAを選択して設定 | Aのみ設定・再起動。Bは変更なし |
| CLI明示選択 | `--device <Aのport>`、`--node-id <AのID>`、両方指定を試す | Aだけ成功。Aのport＋BのIDは送信前に失敗 |
| 画面表示後の交換 | Aのカードを表示後、Aを外してBが旧ttyを取得する状態でAの設定を実行 | 拒否。Bへcredentialを送らない |
| identify後の交換 | 開発debuggerで最初の選択完了後、操作用open前に停止し、A→Bへ交換して再開 | 操作用identify不一致で停止。set_wifi送信なし、Bの設定変更なし |
| 同一接続中の切断 | 開発debuggerで操作用identify後に停止し、抜き差しして再開 | 元接続の失敗。別portへ再openして送信しない |
| clear_wifi | 明示Aで成功を確認し、上記差し替え条件も試す | 正常時はAのみ消去。不一致時はBの保存済み設定・再起動状態とも変更なし |
| firmware最終防御 | テスト用credentialでexpected ID欠落／不一致のv2要求を手動送信 | 保存・消去・再起動なし。適合要求だけ成功 |
| 旧版混在 | 新Gateway＋旧Node、および旧CLI＋新Nodeを試す | 設定失敗。旧protocolへのfallbackや状態変更なし |

差し替え試験ではUSB要求commandの順と保存／消去の有無を確認し、PSK本文をログへ残さない。v1 Nodeも読み取り専用探索で台数に数える。新旧2台が応答した場合も自動選択は拒否し、旧Nodeを明示選択しても操作用v2確認で停止する。
