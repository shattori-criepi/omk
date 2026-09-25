# OMK System Manager

`system-manager`は、Dashboardからホストのハードウェア・systemd操作を直接行わせないための、ホスト側FastAPIサービスです。OMKユーザーとして動作し、Bルート認証情報の更新、Bルートサービスの状態確認・再試行要求、Raspberry Piの再起動・シャットダウン、OMK AP設定の限定参照、USB Node設定、USBへのデータ書き出しを仲介します。

Dashboardは`host.docker.internal:8788`へHTTPで接続し、Bearer tokenはDashboardバックエンドだけが付与します。ブラウザはsystem-manager token、`credentials.yaml`、sudo権限を持ちません。

リポジトリのルートから、OMKホストへインストールします。

```bash
./scripts/setup-system-manager.sh
```

インストーラーは、`/etc/omk/system-manager.env`を所有者`root:root`、mode `0600`で作成します。このファイルには`OMK_SYSTEM_MANAGER_TOKEN`が含まれます。既存tokenは再実行時にも維持します。tokenを表示・commitしたり、ブラウザクライアントへコピーしたりしてはいけません。`/etc/omk/dashboard-system-manager.env`はDashboardコンテナへの環境変数注入だけに用いるファイルで、`credentials.yaml`をコンテナへmountしません。

起動時は`data/site/site_uuid`のUUID v4を使用し、存在しなければ新しく生成して保存します。SORACOM Metadata Serviceへ接続できる場合はSIM Tagの`site_uuid`と照合し、値が異なる場合もローカル値をTagへ反映します。Tagからローカル値を復元する動作ではありません。Metadata Serviceへの接続やTag保存に失敗しても、ローカル値を使って継続するためOnyxは必須ではありません。ローカル値または取得したTagのUUID形式が不正な場合は起動に失敗します。

すべての管理APIでは`Authorization: Bearer <token>`が必要です。token未設定の状態でサービスは起動しません。

エンドポイント:

- `GET /api/broute/credentials/status` は、マスク済みID、パスワード設定の有無、Bルートserviceの稼働状態、および`status.json`由来の接続状態を返します。生のIDとパスワードは返しません。
- `PUT /api/broute/credentials` は、32 byteの表示可能ASCII IDと12 byteの表示可能ASCIIパスワードを受け取ります。mode `0600`のYAMLファイルへatomicに保存し、その後Bルートサービスを再起動して状態を確認します。
- `POST /api/broute/retry` は、長時間未検出時の待機状態だけで利用できる固定の即時再試行要求です。
- `POST /api/system/reboot` は、OMKホストを再起動します。
- `POST /api/system/shutdown` は、OMKホストをシャットダウンします。
- `GET /api/access-point/status` は、NetworkManagerの`omk-ap`プロファイルからSSIDと設定状態だけを返します。
- `GET /api/access-point/credentials` は、NetworkManagerから都度読んだSSID/PSKを認証済みDashboardバックエンドだけへ返します。PSKは保存・ログ出力せず、sudoersも固定の`nmcli --show-secrets ... omk-ap`コマンドだけを許可します。
- `GET /api/nodes/usb-candidates` と `POST /api/nodes/usb-provision` は、接続済みのOMK Nodeを`identify`で確認してから固定の`omk-ap`設定を送信し、MQTT registrationまで確認します。APIは任意のコマンド、profile、brokerを受け取らず、POSTされたdevice/node IDを再探索で照合し、操作用に開いた同じserial接続で再identifyしてから送信します。USB protocol v2の`expected_node_id`をfirmwareが保存前に検証します。異なる物理portから同じNode IDが返る場合は候補探索も操作も拒否します。旧firmwareへのfallbackはありません。PSKはresponse・ログへ返しません。
- `GET /api/export/usb/status`はUSBと書き出し処理の状態および不透明なidentity tokenを返し、`POST /api/export/usb`はそのtokenを要求して固定の`omk-export-usb`コマンドで書き出しを開始します。確認後にUSBが交換された場合は拒否します。CSV/ZIP生成とmount権限の詳細は[data-exporter README](../data-exporter/README.md)を参照してください。

`status.json`は`starting`、`adapter_missing`、`adapter_initializing`、`scanning`、`authenticating`、`connected`、`scan_error`、`authentication_error`、`connection_error`、`retry_wait`、`status_unavailable`、`stopped`をDashboardへ中継します。ID、パスワード、内部例外全文は状態ファイル・API・ログへ出力しません。

sudoersで許可するのは、固定された`systemctl restart omk-broute-meter.service`、`systemctl is-active omk-broute-meter.service`、`systemctl reboot`、`systemctl poweroff`、および`omk-ap`のPSKだけを読む固定`nmcli`呼出しです。APIからコマンドやunit名を受け取らず、`shell=True`も使用しません。

テスト実行:

```bash
cd services/system-manager
pytest -q
```

## USB Node setup

`POST /api/nodes/usb-setup` (202) と `GET /api/nodes/usb-setup/status` により、AtomS3 Liteの固定prebuilt firmware書込みとWi-Fi provisioningを非同期に実行します。既存USB provision APIと同じoperation/serial locksを共有します。導入時にsetup scriptでvenvのesptool 4.11.0と実行ユーザーのdialout membershipを設定します。非root動作と既存sudoers境界は維持します。[package生成・identity guard・復旧方針](../../docs/decisions/usb-node-setup.md)を参照してください。


`POST /api/nodes/usb-reinitialize`は、使用済みNodeの意図的な再セットアップ専用です。bodyは`device`・`node_id`・厳密なbooleanの`confirm_reinitialize: true`だけを受け付け、通常の`usb-setup`とは別requestとして扱います。同じoperation/serial locksとstatus endpointを共有し、Nodeの旧PoP・Wi-Fi・Logical IDを置換/クリアする確認をDashboardで表示します。通常の`usb-setup`では、既存Nodeに有効なGateway credentialがない場合や再セットアップが途中の場合は拒否し、新PoPを無条件に作りません。

USB候補は既存OMK firmware応答の有無（`kind`）に加え、`credential_state`を`missing`・`present`・`invalid`・`reinitialize_pending`で返します。未使用であることはUSB identityだけでは証明できないため、初回のAtomS3 Lite実物確認は維持します。応答しない使用済み/途中状態でも、明示的な再セットアップへ進めます。

再セットアップは固定packageを検証し、現在のAP credentialを取得してから、Gatewayへの新credentialのatomic保存、専用`OMKR` record書込み、firmware書込み、同じNodeの再認識、USBによるPoP/未登録状態の検証、通常USB Wi-Fi provisioning、新しい`provisioned` MQTT status確認の順に進めます。Node IDは各書込み境界とUSB操作で照合します。Gateway credentialの`setup_state=reinitialize_pending`は成功時だけ解除し、失敗時は同じ新PoPで再試行します。古いPoPのバックアップは不要です。

配置済みfirmwareが再セットアップに未対応の場合は`reinitialize_firmware_required`で書込み前に停止します。source commit後に通常のprebuilt生成手順で更新します。未commitの実機確認には`flash-omk-node.sh --reinitialize`がローカルbuildを使用できます。NVSの具体的な変更範囲とCLI前提は[firmware README](../../firmware/esp32/omk-node/README.md#factory-flashとpartition-table)を参照してください。
