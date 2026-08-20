# OMK Mosquitto

OMK Gateway内のMQTT brokerです。標準Gateway構成ではリポジトリルートの
`compose.yaml`から`mosquitto`サービスとして起動し、Nodeとhost service、各Compose
serviceのメッセージを中継します。

- `config/mosquitto.conf`: broker設定。Composeから読み取り専用でmountする。
- `data/`: Mosquittoの永続データ領域。実行時データはGit管理せず、空ディレクトリを
  保つ`.gitkeep`だけを追跡する。

通常はGatewayセットアップを通して起動します。単体確認はリポジトリルートで行えます。

```bash
docker compose up -d mosquitto
docker compose logs --tail=100 mosquitto
```
