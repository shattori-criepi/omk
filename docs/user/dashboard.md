# Dashboardの使い方

DashboardはGatewayの`/display`で現在値・履歴を表示します。Gateway自身では`http://localhost:8000/display`、OMK AP接続端末では`http://192.168.50.1:8000/display`を開きます。

右上の歯車から管理メニュー（`/admin`）を開けます。

## 管理メニュー

- **センサ管理**: BLEセンサの探索、登録、名称・有効状態の確認。
- **Bルート設定**: BルートID・パスワードと接続状態の確認。Bルートを使う場合だけ設定します。
- **システム操作**: Gatewayの再起動・シャットダウン。確認画面をよく読んで実行します。
- **OMK AP情報**: 接続用SSID、必要時のPSK表示、QRコードの確認。
- **表示設定**: `clock`、推奨表示（recommended）、自由に選ぶcustom表示を切り替え、表示項目・順序・サイズを保存します。

管理機能はGateway上のsystem-managerを介して実行されます。system-managerはBルート専用ではありません。

画面のデータが更新されない場合は[トラブルシューティング](troubleshooting.md)を参照してください。API、コンテナ起動、開発用テストは[Dashboard README](../../services/dashboard/README.md)を参照してください。
