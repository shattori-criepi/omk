# SORACOM Onyx セットアップ

## 対象と前提

この手順は、GatewayからSORACOM Harvestへ計測データをアップロードする外部通信回線として、
Raspberry Pi 4（64-bit Raspberry Pi OS、GUIあり）へ USB 接続したSORACOM Onyxを、
NetworkManager と ModemManager で使用するためのものです。ESP32等のセンサノードとの通信には使用しません。実機での確認対象は Quectel EG25-G 系モデム（例: USB ID `2c7c:0125`）、QMI デバイス
`cdc-wdm0`、ネットワークインターフェース `wwan0`、シリアルポート
`/dev/ttyUSB0`～`/dev/ttyUSB3`です。EC25/EG25 系や同様の構成も診断対象にしますが、
USB ID だけでは判定しません。

先に [Gatewayセットアップ](user/gateway-setup.md)を完了し、インターネットへ接続できること、実行ユーザーが `sudo` を使えることを確認してください。LTE 設定は基本セットアップとは別であり、Onyx を使わない環境には不要です。

SIM の金属端子の向きは Onyx 本体の表示に従って確認し、電源を切った状態で確実に挿入してください。ICCID、IMSI、IMEI をリポジトリ、チケット、ログへ記録しないでください。

## 1. USB 認識を確認する

Onyx を Raspberry Pi の USB ポートへ接続し、数秒待ってから確認します。

```bash
lsusb
ls /dev/ttyUSB*
ls /dev/cdc-wdm*
ip link show wwan0
```

代表例は次のとおりです。

```text
2c7c:0125 Quectel Wireless Solutions Co., Ltd. EC25 LTE modem
```

`cdc-wdm0`、`wwan0`、`ttyUSB*`が直ちにすべて現れない場合でも、次のセットアップが
ModemManager の認識を待ちます。Onyx が未接続のまま実行すると、無限待機せず診断を出して終了します。

## 2. セットアップを実行する

通常の SORACOM Air SIM は APN `soracom.io` を使用します。リポジトリのルートで実行します。

```bash
chmod +x scripts/setup-soracom-onyx.sh
./scripts/setup-soracom-onyx.sh
```

APN は引数または環境変数で明示できます。plan-DU の場合だけ `du.soracom.io` を指定します。通常の SIM で plan-DU 用 APN を指定しないでください。

```bash
./scripts/setup-soracom-onyx.sh --apn soracom.io
./scripts/setup-soracom-onyx.sh --apn du.soracom.io
SORACOM_APN=soracom.io ./scripts/setup-soracom-onyx.sh
```

スクリプトは `network-manager`、`modemmanager`、`usb-modeswitch`、`curl`、CA 証明書等を導入し、NetworkManager と ModemManager を有効化・起動します。`mmcli` の有無とサービスの状態を個別に確認するため、SORACOM 公式スクリプトのパッケージ判定だけには依存しません。

その後、SORACOM 公式の
[`setup_eg25.sh`](https://soracom-files.s3.amazonaws.com/connect/setup_eg25.sh)を一時ディレクトリへ取得して実行します。公式スクリプト自体は OMK リポジトリへ保存・改変しません。公式スクリプトは通常の SORACOM Air SIM 向けに引数なしで実行します。別 APN を指定した場合も、公式スクリプト実行後にこのスクリプトが生成済みの NetworkManager プロファイルへ APN を設定します。公式スクリプトが最後に成功表示しても、終了コードだけでは成功とせず、以下の独自確認を行います。

- `soracom` NetworkManager プロファイルと APN の確認・必要時のみ修正
- `connection.autoconnect yes` の確認
- ModemManager によるモデム/SIM、登録、パケット接続状態の確認
- `wwan0` の IPv4 アドレス確認
- SORACOM と外部 IPv4/DNS/HTTPS の疎通確認

既存の `soracom` プロファイルや `/etc/NetworkManager/dispatcher.d/90.soracom_route` を無条件に削除しません。APN や自動接続を変更したときは、変更前後をログへ記録します。
Wi-Fi や有線 LAN を切断したり、既定経路を Onyx へ強制変更したりもしません。

ログは `~/.local/state/omk/logs/soracom-onyx-YYYYmmdd-HHMMSS.log` に保存され、Git 管理対象外です。モデム識別子はログでマスクします。

## 3. 成功を確認する

完了後、主な確認コマンドです。

```bash
nmcli connection show --active
nmcli device status
mmcli -L
mmcli -m any
ip -4 address show wwan0
ping -c 4 pong.soracom.io
ping -I wwan0 -c 4 8.8.8.8
```

`soracom` がアクティブ、`cdc-wdm0` または `wwan0` が `connected`、`wwan0` に IPv4
アドレスがあることを確認します。`mmcli -m any` では SIM が見え、`registration` が
`home` または `roaming`、`packet service state` が `attached`、モデムが接続状態であることを確認します。`ping pong.soracom.io`、IPv4、モデム登録状態が主な成功判定です。
`ping -I wwan0 -c 4 8.8.8.8` と次の HTTPS 確認は補助的な確認です。

```bash
curl --interface wwan0 -4 --max-time 20 https://ifconfig.me
```

Wi-Fi と Onyx を同時に使う場合、通信に使用した経路を確認します。

```bash
ip route get 8.8.8.8
ip route get 8.8.8.8 oif wwan0
nmcli device status
```

スクリプトは既定経路を変更しないため、一般的な通信が Wi-Fi を使うことがあります。Onyx の確認では `-I wwan0` や `--interface wwan0` を付けます。

## 4. 再起動・抜き差し後

`soracom` プロファイルは自動接続を有効にします。再起動後、または Onyx を抜き差しして再認識させた後に、次を確認します。

```bash
nmcli connection show soracom
nmcli connection show --active
nmcli device status
mmcli -L
ip -4 address show wwan0
```

必要なら `sudo nmcli connection up soracom` を実行します。セットアップスクリプトは
Raspberry Pi を自動再起動しません。認識しない場合は、Onyx の抜き差しまたは再起動を行ってから再確認してください。

## 5. トラブルシューティング

`mmcli` がない場合は `modemmanager` が入っていないか、導入が不完全です。次を実行してサービスを起動してからスクリプトを再実行します。

```bash
sudo apt-get update
sudo apt-get install -y modemmanager
sudo systemctl enable --now ModemManager
command -v mmcli
systemctl is-active ModemManager
```

NetworkManager の接続コマンドがタイムアウトしても、数十秒後に接続が完了することがあります。状態を再確認し、ログと次の診断を確認してください。

```bash
nmcli connection show soracom
nmcli connection show --active
nmcli device status
mmcli -m any
sudo journalctl -u ModemManager -n 50 --no-pager
```

`searching`、`detached`、または `registration: searching` のままの場合、信号品質が良くても
SIM 契約が原因で接続できないことがあります。自動修復を試みず、次を確認してください。

- SIM が利用開始済みで、休止・解約・利用中断になっていないか
- SORACOM コンソール上で SIM が有効か
- APN が SIM プランに一致するか（plan-DU は `du.soracom.io`）
- SIM の向きや接触に問題がないか

SIM のローカル診断には `mmcli -i 0` を使えますが、表示された ICCID/IMSI を共有しないでください。

終了コードは、`0` 成功、`1` 一般エラー、`2` Onyx 未検出、`3` ModemManager 未認識、`4` SIM
未検出、`5` 携帯網未登録、`6` `wwan0` の IPv4 未取得、`7` SORACOM 疎通失敗です。

Wi-Fi アクセスポイント化、NAT、既定経路の切替はこのスクリプトの範囲外です。別のネットワーク設定スクリプトとして扱ってください。
