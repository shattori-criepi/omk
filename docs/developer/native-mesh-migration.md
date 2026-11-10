# Native Mesh実装・移行資料

2026-11-10。実装とオフライン確認のみ。実機への書込み、運用サービス変更、GitHub pushは行っていない。

## 作業の保全と保存版

実機試験対象は `experiment/native-mesh-p1-20261110` ブランチの保存commitで固定する。
[成果物・再現手順](../../experiments/native-mesh-p1-20261110/README.txt)、
[来歴とSHA-256](../../experiments/native-mesh-p1-20261110/manifest.json)、
[実機移行手順（公開版）](../../experiments/native-mesh-p1-20261110/HARDWARE-TRIAL-ja.txt)を参照。

P1修正後の独立コピーと配布済みAtomS3 Lite BIN/ELFを照合して収録した。元checkoutの未コミット変更と保存済みfirmwareは変更していない。無関係な診断開発・実機記録・認証情報は含めない。既存公開用prebuiltは旧方式のまま保持し、この試験の配布物と区別する。

## 実装内容

| 範囲 | 変更 |
| --- | --- |
| Node通信 | 仮想netif、DHCP server、NAPT、IP encapsulation、Node別MQTT、独自recovery/restart、詳細診断patchを削除 |
| native転送 | 公式Mesh send/recv、固定message種別、有限queue、Root専用MQTT、役割epoch、独立RX/送信/設定worker |
| Node状態 | 30秒heartbeat、永続登録revision、SEN66計測とBLE rawを既存topicへ変換できるJSONで送る |
| BLE | Active scan 100ms/50ms、環境値25秒、認識済みイベント変化時送信、Wi-Fi/BLEのcore分離 |
| Gateway | 希望状態を保存して設定再送、適用結果で収束、120秒受信鮮度による可用性 |
| collector | protocol/revision/seqを測定項目候補から除外。元JSONと測定値は維持 |
| 維持 | SEN66/I2Cドライバ、sensor復旧、BLE decoder、Node identity、Wi-Fi credential、USB provisioning、管理HTTP API |

新規の独自処理は、既存topicへの変換、有限受付、MQTTとMeshの役割分離、登録状態の照合に限定する。中間Nodeの経路制御、測定値の汎用ACK/retry、ネットワーク異常時の独自再起動はない。`mesh_session`はSDK eventから現在の役割・接続世代を追跡する小さな状態処理であり、自己組織化の代替ではない。

独立レビューP1修正では、解除・変更適用までのID予約、GatewayでのSEN66所有者/revision照合後の共通topic転送、接続中のみの有限・重複抑制付き設定再送、Presence Sensor Proの`00/20`状態変化優先送信を追加した。SEN66の内部topicは`omk-relay/<node_id>/sen66/raw`で、公開topic/payloadは維持する。修正前native版Rootはこの照合を経由しないため混在させず、Gatewayと全Nodeを修正版へ揃える。

Mesh ID/PSK導出ドメインはv2へ変更した。旧IP転送とnative転送が混在して誤ったRootを選ぶことを防ぐためであり、新旧の通信互換はない。

## オフライン確認（P1修正後）

- Firmware + BLE Sensor Manager: 491件成功。
- sensor-collector: 11件成功。
- 周辺scripts: 234件成功。USB関連4 suiteの132件はこの234件に含み、重複計上しない。
- 合計736件成功。AtomS3 Lite build成功（ESP-IDF 6.0.1、static RAM 72,880 bytes、flash 1,533,957 bytes）。
- 既存fixture `test_host_mqtt_persistence.py` と `test_final_review_mutations.py` の2件は実行未完了。収集時に既存B-route fixtureがsudoへ進み、コンテナ制約で停止した。成功数に含めない。
- 実kernel/network namespaceが必要な `test_ap_socket_kernel.py` はオフライン対象外。
- P1差分再レビューで新規P0/P1なし。実機未検証。今回のGit保存時には追加テスト・再ビルドを行っていない。

解除/連続ID変更・旧Root滞留値・Node不在・Gateway再起動、Paho 2.1.0切断中1000周期、Presenceの1秒後検知→5秒後非検知を回帰確認済み。過去の修正前NativeやM5Stick-C buildを、この試験BINの検証実績として扱わない。

既存ログと再実行手順は成果物ディレクトリを参照。host testsは実RTOS、RF共存、多段Mesh、Root交代、長時間安定性を証明しない。build RAMはstatic使用量である。

## 導入時の変更範囲

1. GatewayのBLE Sensor Managerとsensor-collectorを更新する。Dashboard、Broker、transformer、Harvestのtopic/測定fieldは維持し、個別の変換改修は不要。通常のサービス再起動は導入時に行う。
2. 同じ通信経路を構成する全Nodeへ同じnative firmwareを導入する。旧Nodeは新Meshを中継できず、旧Gatewayの登録protocolも使えないため、更新中の欠測時間を設ける。
3. 保存済みWi-Fi credentialとNode IDは維持する。旧logical IDはrevision 0として読み出す。Gatewayの旧pending変更/解除は新revisionへ移し、Node状態から再同期する。
4. 保存済みprebuilt/配布packageは今回更新していない。Dashboardから旧prebuiltを再書込みすると旧方式へ戻るため、配布packageの更新と対象Nodeの版確認を導入作業に含める。

MQTT登録はprotocol v2、論理device可用性のpublisherはGatewayへ変わる。旧NodeのLWTが移行後に遅れて届かないよう、旧運用Nodeを停止した状態で揃えて切り替える。旧診断topicの履歴やretained値は新しい可用性の根拠にしない。

登録変更後に旧firmwareだけへ戻すと、新しいNVS登録blobを読まず古いIDを参照し得る。撤退は通信経路上の全NodeとGatewayを一組で戻し、保存したNode登録一覧に照らしてIDを確認する。Wi-Fi/NVS全消去による回避を通常手順にしない。

## 最小の実機確認と導入判断

本実装では以下を実行していない。運用データを失わない試験環境で一度だけ確認する。

1. 2段以上の実際のMesh経路で、全NodeのBLEとSEN66を24時間継続。既存Dashboard/JSONLに周期値が入り、Node状態が30秒程度で届くことを確認する。
2. 代替経路がある配置で親Node停止とRoot停止を各1回行う。SDKによる親/Root変更後、180秒以内に計測・状態・登録同期が戻ることを確認する。代替無線経路がない孤立は不合格理由から区別する。
3. Node不在中にID変更または解除を要求し、Gateway/Broker再起動後にNodeを戻す。最新希望状態へ収束し、古いACKやretained値で戻らないこと、無受信120秒でofflineになることを確認する。
4. Motion/Contactを各数回操作し、状態変化が25秒の環境値周期へ吸収されないことを確認する。受信不能なBLE広告は復元できないため、短時間の変化の取りこぼしも確認対象とする。

通常時に120秒超の欠測が繰り返す、代替経路があるのに180秒以内に自律復帰しない、登録が収束しない、panicが再発する、またはイベント欠落が運用要件を満たさない場合は、本構成の本番導入を見送る。独自watchdog・再接続制御・診断を追加し続けることを前提にしない。

旧仮想netif/DHCP serverの経路は除去したが、BLE/SoftAP共存、SDK内memory不足、無応答の親をSDKが切り替えられない状況は残り得る。安定性改善を保証する成果物ではなく、単純化した構成の導入候補である。
