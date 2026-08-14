# OMK System Manager

`system-manager` は、Bルート認証情報を更新するためのホスト側FastAPIサービスです。OMKユーザーとして実行し、書き込む対象を`broute-meter/config/credentials.yaml`のみに限定します。また、sudoersで許可された`omk-broute-meter.service`向けの2つの`systemctl`コマンドだけを実行します。

リポジトリのルートから、OMKホストへインストールします。

```bash
./scripts/setup-system-manager.sh
```

インストーラーは、`/etc/omk/system-manager.env`を所有者`root:root`、モード`0600`で作成します。このファイルには`OMK_SYSTEM_MANAGER_TOKEN`が含まれます。tokenを表示・commitしたり、ブラウザクライアントへコピーしたりしてはいけません。すべてのBルートAPIエンドポイントでは、`Authorization: Bearer <token>`が必要です。

エンドポイント:

- `GET /api/broute/credentials/status` は、マスク済みIDとパスワード設定の有無だけを返します。
- `PUT /api/broute/credentials` は、32 byteの表示可能ASCII IDと12 byteの表示可能ASCIIパスワードを受け取ります。モード`0600`のYAMLファイルへatomicに保存し、その後Bルートサービスを再起動して状態を確認します。

テスト実行:

```bash
cd services/system-manager
pytest -q
```
