# リポジトリ構成

現行の主要ディレクトリだけを示します。将来構想の`core`、`ui`、`simulator`、`apps/core/src`は存在しません。

| パス | 役割 |
|---|---|
| `services/` | Dashboard、collector、Bルート、system-manager、BLE manager、data-transformer、Harvest、パワコン連携などのサービス |
| `firmware/` | ESP32共通Nodeと既存SEN66専用Nodeのfirmware |
| `scripts/` | Gateway、Node、各host serviceのセットアップ・診断スクリプト |
| `systemd/` | host systemdおよびuser systemd unit template |
| `docs/` | 利用者・開発者・判断履歴・history文書 |
| `hardware/` | PCB、配線、筐体など再現可能なハードウェア成果物 |
| `data/` | Gateway実行時データの配置規約。実測データはGit管理しない |
| `compose.yaml` | 現行GatewayのDocker Compose定義 |
| `compose.dev.yaml` | Bルートmock・container test用の開発Compose定義 |

`docs/history/`は再開発過程の構想・ロードマップです。現行仕様は`docs/user/`と`docs/developer/`、コンポーネント単体の詳細は各READMEを参照してください。
