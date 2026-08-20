# 開発ガイド

OMKはPython services、Docker Compose、host systemd、ESP-IDF firmwareで構成されています。日常開発はWindows + WSL2でも行えますが、Gateway固有のNetworkManager、BLE、USB、systemdはRaspberry Pi実機で確認します。

## 基本環境

- Python service: 各`services/*/requirements*.txt`とREADMEに従う
- ESP32 Node: ESP-IDF/PlatformIO環境と[共通Node README](../../firmware/esp32/omk-node/README.md)
- Gateway: Raspberry Pi 4/5、64-bit Raspberry Pi OS、Docker Compose
- 実運用構成: `compose.yaml`。Bルートのmock・container testは`compose.dev.yaml`を明示して実行する。`compose.mock.yaml`、`compose.windows-hardware.yaml`、`compose.pi.yaml`は現行リポジトリに存在しない

## Gatewayでの確認

新規Gatewayは`./scripts/setup-omk-gateway.sh --with-base`、既存Gatewayの再実行は`./scripts/setup-omk-gateway.sh`を使います。標準Composeサービスは次です。

```bash
docker compose ps
docker compose logs --tail 100 mosquitto sensor-collector dashboard harvest-uploader
curl http://localhost:8000/health
```

host serviceは対象を明示して確認します。

```bash
sudo systemctl status omk-system-manager.service --no-pager
sudo systemctl status omk-data-transformer.timer --no-pager
```

## テスト

各サービスの単体テストは対応READMEに従います。リポジトリ全体では、変更対象に応じてPython test、shell setup test、Compose設定検証を実行します。実機依存のBLE、Bルート、LTE、kioskは、対応ハードウェアを使ってRaspberry Pi上で検証します。

秘密情報、実測データ、SSID/PSK、Bルート認証情報、API tokenをテスト出力・fixture・Gitへ含めません。変更後は少なくとも対象テスト、`git diff --check`、関連文書リンクを確認してください。

## 変更の境界

コンポーネント単体の開発・デバッグは各READMEを正本とします。全体設計は[アーキテクチャ](architecture.md)、MQTT契約は[データ経路とMQTT](data-and-mqtt.md)、AP設計は[ネットワーク設計](networking.md)を参照してください。
