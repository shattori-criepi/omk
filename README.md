# OMK — おうちモニタキット

OMK（おうちモニタキット）は、住宅内の電力・環境・行動に関するデータを、必要なセンサを組み合わせて計測、表示、保存、外部送信するためのオープンなIoT基盤です。Raspberry PiをGatewayとして使い、住宅側ネットワークに依存せずに運用できる構成を目指します。

## できること

- Raspberry Pi 4/5上のGateway、OMK専用Wi-Fi AP、MQTTによるセンサ集約
- JSONL一次保存、Parquet変換、Dashboardでの瞬時値・履歴表示
- Bルート、BLEセンサ、ESP32 Node、SORACOM Harvest連携
- 任意の住宅用PV・蓄電池・PCS連携（現行は単一の検証profile向け `ichijo-energy-node`）

```text
センサ / Bルート / PV・蓄電池・PCS / BLE
             ↓ MQTT
Gateway → sensor-collector → JSONL → Parquet → Dashboard
             └ harvest-uploader → SORACOM Harvest
```

## OMKを使う

新規導入の全体像と必要機材は[利用開始ガイド](docs/user/getting-started.md)を参照してください。新規Gatewayでは次が標準入口です。

```bash
./scripts/setup-omk-gateway.sh --with-base
```

既存Gatewayの再実行では、OS/Docker基盤を更新しないよう通常は`--with-base`を付けません。詳しくは[Gatewayセットアップ](docs/user/gateway-setup.md)を参照してください。

- [Nodeとセンサ](docs/user/node-and-sensors.md)
- [Dashboardの使い方](docs/user/dashboard.md)
- [利用者向けトラブルシューティング](docs/user/troubleshooting.md)

## OMKを開発する

- [アーキテクチャ](docs/developer/architecture.md)
- [開発環境・テスト](docs/developer/development.md)
- [ネットワーク設計](docs/developer/networking.md)
- [データ経路とMQTT](docs/developer/data-and-mqtt.md)
- [リポジトリ構成](docs/developer/repository-structure.md)

各サービスとfirmwareのREADMEは、単体の起動、設定、テスト、デバッグの正本です。

## 現在の対応範囲

- 正式対象: Raspberry Pi 4/5、64-bit Raspberry Pi OS
- Gateway: Docker Compose、host systemd、NetworkManager
- Node: 共通ESP32 firmware（SEN66計測、BLE relay、USB Serial/JTAG Provisioning）
- 任意機能: SORACOM Onyx、BLEセンサ、Bルート、GUI kiosk、住宅用PV・蓄電池・PCS連携

MQTTは現在、OMK専用LAN内の匿名・平文接続です。認証、ACL、TLSは将来課題です。

## 利用上の注意

OMKは研究・実験用途のシステムです。計測値の正確性、完全性、継続性、対応機器との互換性、安定動作および安全性は保証しません。利用、改変、機器への接続、再配布は各利用者の判断と責任で行ってください。料金精算、契約上の計量、安全制御、保護制御など、高い信頼性を要する用途での利用は想定していません。研究所および開発者は、個別のサポート、保守、動作保証を提供するものではありません。将来正式なOSS licenseを追加した場合は、その無保証・責任制限条項も適用する予定です。

## サポートと開発方針

OMKは研究・実験用のオープンな計測基盤であり、一般利用者向けの商用製品やサポートサービスではありません。GitHub等から利用する一般利用者は、原則として自ら機器を準備し、構築、設定、運用、保守します。活用例、アイデア、改善案など、OMK全体に有用な情報の共有は歓迎します。ただし、個別の利用方法に関する質問への回答やサポートを約束するものではありません。相談・
要望を受けたこと自体は、個別の開発、機器対応、サポートを約束するものではありません。

特定の利用者だけに必要な機能は、必要に応じて利用者自身のforkで開発・維持することを想定します。一方、OMK全体にとって有用性が高いアイデアは、研究目的、汎用性、保守性、開発優先度などを踏まえ、maintainer側で採用・実装する場合があります。採用する場合も、提案者へPR作成を求めるとは限らず、maintainer側で実装する場合、または実装しない場合があります。

Issueは単なる機能追加要望の受付窓口ではなく、再現可能な不具合や、具体的に対応することになった課題の管理に用います。一般的なアイデア、活用例、利用方法に関する情報共有には、その前段となるコミュニケーションの場を設ける方針です。将来の第一候補はGitHub Discussionsですが、無償サポート窓口として位置づけるものではありません。現時点でGitHub設定は変更していません。コード変更や機能追加を提案したい場合は、まず目的、背景、想定用途、OMK全体への有用性を共有してください。maintainerが本体への取り込みを検討する価値があると判断した場合に、必要に応じて
Issue化またはPR提出を案内します。PRは常時募集する入口ではなく、事前相談のないPRについて
review、merge、対応は約束しません。

電中研等が主体となる研究・実証・被験者実験でOMKを設置する場合は、一般OSS利用とは別扱いです。その場合は研究計画に基づき、研究実施者側が必要な設置、運用、保守、トラブル対応等を行う場合があります。上記の自己責任・一般サポート方針は、実験協力者へ保守責任を転嫁する意味ではありません。

## 開発状況と履歴

現行仕様は`docs/user/`と`docs/developer/`を正本とします。再開発時の構想・工程記録は[history](docs/history/)に分離しており、現行仕様ではありません。設計採否の理由は[decisions](docs/decisions/)に残します。

ライセンスは現在選定中です。公開・利用条件は決定後にリポジトリへ明記します。
