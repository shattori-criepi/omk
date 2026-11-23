# Native ESP-WIFI-MESH通信

AtomS3 Liteの開発版はESP-IDF 6.0.1のESP-WIFI-MESHを使用する。2026年11月22〜23日の試験ではSEN66付き2台と中継専用2台が同じアプリで稼働した。どのNodeもRootに選出され得る。

| 役割 | SEN66の計測と配送 | BLEとMesh |
| --- | --- | --- |
| 非Root | 計測結果を有限キューに渡し、専用タスクが `esp_mesh_send()` でRootへ送る | SwitchBot広告をスキャンし、観測をMeshで中継する。中間NodeのルーティングはSDKが担う |
| Root | 自身の計測と子Nodeから受け取った計測を同じMQTT経路へ渡す | ローカルの広告スキャンを停止し、子Nodeから届くBLE観測をMQTTへ渡す |
| Gateway | 登録済みの物理Node ID・Logical ID・revisionを照合してSEN66の公開トピックへ転送する | BLE Sensor Managerが中継観測をデコードし、direct受信と同じセンサ台帳を使う |

RootだけがGateway APでIPを取得し、MQTTブローカーに接続する。非Rootには転送用のIPアドレスもMQTTセッションも要らない。Root交代時は接続世代を更新し、新RootがMQTTの購読を開始する。SEN66の計測はMesh送信を待たず、キュー満杯時や期限切れでは欠測が生じ得る。

## 配送と登録

RootはSEN66を `omk-relay/<node_id>/sen66/raw`、BLE観測を `omk-relay/<node_id>/ble/raw` に送る。Gatewayは登録が一致するSEN66のpayloadをそのまま `omk/<logical_id>/sen66` へ転送する。設定はGatewayが保持する希望revisionをRoot経由で配送し、NodeがNVSへ適用したrevisionを返す。登録設定と応答はQoS 1、計測・BLE観測はQoS 0である。[MQTTトピックとペイロード](../developer/data-and-mqtt.md)

旧prebuiltとNative Meshは、同じGateway SSID・PSKから異なるMesh IDとMesh APパスワードを導出する。旧版のNodeはNative Meshの親・中継になれない。GatewayのBLE Sensor Managerは両世代のトピックを扱う。

## 復旧と運用実績

非Rootでは送信進捗とRootの状態受領を別々に監視する。送信停滞時は検証済みの条件で親再選択を要求する。SDKの送信呼び出し自体が戻らない場合の限定再起動は、SEN66稼働中の子Nodeと `CONFIG_OMK_RELAY_RESTART_NODE_ID` で指定した中継専用機に限る。AtomS3 Liteの通常設定は実機検証時の1台を指定している。空文字では中継専用機の例外を無効にできる。

11月22日20時〜23日16時11分頃、SEN66-001（Root）は安定稼働し、SEN66-002（Layer 3）の最長受信間隔は264.6秒だった。この間、SEN66-002のboot IDは一定で、親再選択要求3回が受理された。短時間の欠測とSDK内部の送信停止については運用上の制約として扱う。[Issue #67のクローズ記録](https://github.com/shattori-criepi/omk/issues/67#issuecomment-7094562833)
