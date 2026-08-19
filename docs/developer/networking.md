# Gatewayネットワーク設計

この文書はOMK APとGatewayのネットワーク境界の正本です。利用者向けの設定手順は[Gatewayセットアップ](../user/gateway-setup.md)を参照してください。

## 境界

- Gateway自身は`wwan0`、EthernetなどのWAN経路からInternetへ接続できる。
- OMK AP clientはGatewayのローカルサービスへ接続できる。
- Dockerが公開するMQTT（`192.168.50.1:1883`）とDashboard（`192.168.50.1:8000`）にも接続できる。
- AP clientをWANや他の外部インターフェースへ転送しない。
- AP側IPv6は無効にする。

`NetworkManager`の`ipv4.method shared`はDHCP払い出しに使うだけで、Internet共有を目的にしません。shared-mode dnsmasqには`no-resolv`を設定し、AP clientの上流DNS解決も停止します。

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
