# リポジトリ構成

主要なディレクトリと構成ファイルを示します。

| パス | 役割 |
|---|---|
| `services/` | Dashboard、collector、Bルート、system-manager、BLE manager、data-transformer、Harvest、住宅用PV・蓄電池・PCS連携などのサービス |
| `firmware/` | SEN66計測とBLE中継に使うESP32共通Nodeのfirmware |
| `scripts/` | Gateway、Node、各host serviceのセットアップ・診断スクリプト |
| `systemd/` | host systemdおよびuser systemd unit template |
| `docs/` | 利用者向け手順、開発者向け設計、設計判断の記録 |
| `hardware/` | PCB、配線、筐体など再現可能なハードウェア成果物 |
| `data/` | Gateway実行時データの配置規約。実測データはGit管理しない |
| `compose.yaml` | 現行GatewayのDocker Compose定義 |
| `compose.dev.yaml` | Bルートmock・container test用の開発Compose定義 |

現行仕様は`docs/user/`と`docs/developer/`、コンポーネント単体の詳細は各READMEを参照してください。
