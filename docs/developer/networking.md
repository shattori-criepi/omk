# Gatewayネットワーク設計

この文書はOMK APとGatewayのネットワーク境界の正本です。利用者向けの設定手順は[Gatewayセットアップ](../user/gateway-setup.md)を参照してください。

## 境界

- Gateway自身は`wwan0`、EthernetなどのWAN経路からInternetへ接続できる。
- OMK AP clientはGatewayのローカルサービスへ接続できる。
- Dockerが公開するMQTT（`192.168.50.1:1883`）とDashboard（`192.168.50.1:8000`）にも接続できる。
- AP clientをWANや他の外部インターフェースへ転送しない。
- AP側IPv6は無効にする。

`NetworkManager`の`ipv4.method shared`はDHCP払い出しに使うだけで、Internet共有を目的にしません。shared-mode dnsmasqには`no-resolv`を設定し、AP clientの上流DNS解決も停止します。

## ESP-WIFI-MESH Node internal network

AtomS3 Lite NodeはGateway APの外側にESP-WIFI-MESH internal IP networkを形成できる。root NodeだけがGateway APへ通常STA接続し、rootは`10.0.0.1/16`でMesh childへDHCP/DNSを提供してNAPTを有効化する。childの`10.0.0.x`通信はrootでNAPTされ、Gateway Mosquittoの`192.168.50.1:1883`へ通常TCPで到達する。

これはGatewayの`wlan0`にMesh専用プロトコルを追加するものではない。Gateway側にはMesh daemon、static route、Node別IP設定を置かず、既存のAP、Docker公開MQTT、nftables境界を維持する。NodeのMesh AP credentialは保存済みGateway SSID/PSKからruntime導出し、Gateway credential、導出値ともログやsourceへ出さない。

共通firmwareではroot/parent/childをNode IDへ固定しない。配置、電波条件、起動順により役割は変わる。2026-08-20のAtomS3 Lite 2台試験ではrootがGateway側IPを取得し、childが`10.0.0.2`を取得して通常ESP-MQTTでMosquittoへ到達した。これは実測例であり、IPや役割の固定仕様ではない。E2E範囲とcredential migrationは[ESP-WIFI-MESH Node networking decision](../decisions/esp-wifi-mesh-node-networking.md)を参照する。

## forwardingルール

`setup-wifi-access-point.sh`は専用nftables tableに、次の順序でルールを導入します。

```nft
iifname "wlan0" ct status dnat accept
iifname "wlan0" oifname != "wlan0" drop
```

Dockerの公開ポートへの接続は、host側でDNATされた後にDocker bridgeへforwardされます。そのため、DNAT済み通信だけを先に許可し、その後にAPからAP外への転送をdropします。bridge名を条件にするとDockerの再作成や環境差で壊れるため、bridge名には依存しません。

この順序により、AP clientからGateway上のMQTT/Dashboardは使えますが、`wwan0`、Ethernet、将来追加するWANへは到達できません。これはセンサ用ローカルネットワークをInternetから分離するための仕様です。

## 現行実装と長期方針

現在のAP既定SSIDは`OMK-XXXXXX`、接続名は`omk-ap`です。これは現行実装のデフォルトであり、中央組織によるOMK ID発行や固定prefixへの長期依存を意味しません。公開版でも第三者が任意の識別子・SSIDで構築できる設計を維持します。

SSIDの`XXXXXX`は`/etc/machine-id`のSHA-256先頭6桁を大文字16進数にしたものです。machine-idを読めない場合はWi-Fi MAC addressを同じ方法でハッシュします。

PSKは初回プロファイル作成時だけ、OSのCSPRNG（`/dev/urandom`）から生成する24文字の英数字です。machine-id、MAC address、SSIDなどのGateway固有値からPSKを導出してはいけません。既存プロファイルの保存済みPSKは最優先で維持し、`OMK_AP_PSK`が指定されていても暗黙に上書きしません。既存PSKが欠落している場合だけ、`OMK_AP_PSK`または新規乱数を設定します。

PSKは`nmcli connection edit`の標準入力で設定し、argvやセットアップログへ出力しません。`--print-config`もPSK本文ではなく`[MASKED]`だけを表示します。
