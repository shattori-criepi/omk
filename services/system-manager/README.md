# OMK System Manager

`system-manager` は、Dashboardからホストのハードウェア・systemd操作を直接行わせないための、ホスト側FastAPIサービスです。OMKユーザーとして動作し、Bルート認証情報の更新、Bルートサービスの状態確認・再試行要求、Raspberry Piの再起動・シャットダウン、OMK AP設定の限定参照を仲介します。

Dashboardは`host.docker.internal:8788`へHTTPで接続し、Bearer tokenはDashboardバックエンドだけが付与します。ブラウザはsystem-manager token、`credentials.yaml`、sudo権限を持ちません。

リポジトリのルートから、OMKホストへインストールします。

```bash
./scripts/setup-system-manager.sh
```

インストーラーは、`/etc/omk/system-manager.env`を所有者`root:root`、mode `0600`で作成します。このファイルには`OMK_SYSTEM_MANAGER_TOKEN`が含まれます。既存tokenは再実行時にも維持します。
tokenを表示・commitしたり、ブラウザクライアントへコピーしたりしてはいけません。起動時には、`data/site/site_uuid` とSORACOM Metadata ServiceのSIM Tag `site_uuid` を照合します。片方だけにあるUUID v4はもう片方へ復元・保存し、両者が異なる、または形式が不正な場合は安全のためサービスを起動しません。Metadata Serviceへ接続できない場合は、検証済みのローカル値があれば警告して継続します。
`/etc/omk/dashboard-system-manager.env`はDashboardコンテナへの環境変数注入だけに用いるファイルで、
`credentials.yaml`をコンテナへmountしません。

すべての管理APIでは`Authorization: Bearer <token>`が必要です。token未設定の状態でサービスは起動しません。

エンドポイント:

- `GET /api/broute/credentials/status` は、マスク済みID、パスワード設定の有無、Bルートserviceの稼働状態、および`status.json`由来の接続状態を返します。生のIDとパスワードは返しません。
- `PUT /api/broute/credentials` は、32 byteの表示可能ASCII IDと12 byteの表示可能ASCII
  パスワードを受け取ります。mode `0600`のYAMLファイルへatomicに保存し、その後Bルートサービスを再起動して状態を確認します。
- `POST /api/broute/retry` は、長時間未検出時の待機状態だけで利用できる固定の即時再試行要求です。
- `POST /api/system/reboot` は、OMKホストを再起動します。
- `POST /api/system/shutdown` は、OMKホストをシャットダウンします。
- `GET /api/access-point/status` は、NetworkManagerの`omk-ap`プロファイルからSSIDと設定状態だけを返します。
- `GET /api/access-point/credentials` は、NetworkManagerから都度読んだSSID/PSKを認証済みDashboard
  バックエンドだけへ返します。PSKは保存・ログ出力せず、sudoersも固定の`nmcli --show-secrets ... omk-ap`
  コマンドだけを許可します。

`status.json`は`starting`、`adapter_missing`、`adapter_initializing`、`scanning`、
`authenticating`、`connected`、`scan_error`、`authentication_error`、`connection_error`、
`retry_wait`、`status_unavailable`、`stopped`をDashboardへ中継します。ID、パスワード、内部例外全文は状態ファイル・API・ログへ出力しません。

sudoersで許可するのは、固定された`systemctl restart omk-broute-meter.service`、
`systemctl is-active omk-broute-meter.service`、`systemctl reboot`、`systemctl poweroff`、および`omk-ap`のPSKだけを読む固定`nmcli`呼出しです。
APIからコマンドやunit名を受け取らず、`shell=True`も使用しません。

テスト実行:

```bash
cd services/system-manager
pytest -q
```
