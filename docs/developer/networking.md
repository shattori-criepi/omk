# Gatewayネットワーク設計

この文書でOMK APとGatewayのネットワーク境界を定義します。利用者向けの設定手順は[Gatewayセットアップ](../user/gateway-setup.md)を参照してください。

## 境界

- Gateway自身は`wwan0`、EthernetなどのWAN経路からInternetへ接続できる。
- OMK AP clientはGatewayのローカルサービスへ接続できる。
- AP clientはMQTT（`192.168.50.1:1883`）とDashboard（`192.168.50.1:8000`）へ接続できる。
- AP clientをWANや他の外部インターフェースへ転送しない。
- AP側IPv6は無効にする。

`NetworkManager`の`ipv4.method shared`はDHCP払い出しに使うだけで、Internet共有を目的にしません。shared-mode dnsmasqには`no-resolv`を設定し、AP clientの上流DNS解決も停止します。

## ESP-WIFI-MESH Node internal network

AtomS3 Lite NodeはGateway APの外側にESP-WIFI-MESH internal IP networkを形成できる。root NodeだけがGateway APへ通常STA接続し、rootは`10.0.0.1/16`でMesh childへDHCP/DNSを提供してNAPTを有効化する。childの`10.0.0.x`通信はrootでNAPTされ、Gateway Mosquittoの`192.168.50.1:1883`へ通常TCPで到達する。

これはGatewayの`wlan0`にMesh専用プロトコルを追加するものではない。Gateway側にはMesh daemon、static route、Node別IP設定を置かず、既存のAP、AP専用MQTT socket、nftables境界を維持する。NodeのMesh AP credentialは保存済みGateway SSID/PSKからruntime導出し、Gateway credential、導出値ともログやsourceへ出さない。

共通firmwareではroot/parent/childをNode IDへ固定しない。配置、電波条件、起動順により役割は変わる。2026-08-20のAtomS3 Lite 2台試験ではrootがGateway側IPを取得し、childが`10.0.0.2`を取得して通常ESP-MQTTでMosquittoへ到達した。これは実測例であり、IPや役割の固定仕様ではない。E2E範囲とcredential migrationは[ESP-WIFI-MESH Node networking decision](../decisions/esp-wifi-mesh-node-networking.md)を参照する。

## forwardingルール

`setup-wifi-access-point.sh`は専用nftables tableに次のルールを導入します。

```nft
iifname "wlan0" oifname != "wlan0" drop
```

ComposeはMQTTとDashboardをそれぞれ`127.0.0.1:1883`、`127.0.0.1:8000`だけへ公開します。AP側には`FreeBind=yes`と`BindToDevice=wlan0`を指定したIPv4 systemd socketを置き、`systemd-socket-proxyd`でloopback backendへ中継します。prepareではunitを配置するだけとし、Composeのloopback移行とbackend healthの後にsocketを起動します。socketはAP addressの付与前に起動できるため、Docker image buildとruntime healthを初期Wi-Fiのまま完了できます。

DockerはAP addressやwildcardへpublishしません。socketは`wlan0`に固定し、forwarding ruleはAPから外部interfaceへの転送をdropします。この組み合わせにより、AP clientからGateway上のMQTT/Dashboardは使えますが、`wwan0`、Ethernet、将来追加するWANへは公開しません。

AP profileのprepare中はinactive profileの`connection.autoconnect`を`no`にします。全health checkとcredential handoff後、固定の`omk-ap-activation.service`がSSH sessionから独立してautoconnectを有効化し、APをactivateします。失敗時はautoconnectを`no`へ戻し、直前のWi-Fi profile UUIDが取得できていれば復帰を試みます。

## 現行実装と長期方針

現在のAP既定SSIDは`OMK-XXXXXX`、接続名は`omk-ap`です。これは現行実装のデフォルトであり、中央組織によるOMK ID発行や固定prefixへの長期依存を意味しません。公開版でも第三者が任意の識別子・SSIDで構築できる設計を維持します。

SSIDの`XXXXXX`は`/etc/machine-id`のSHA-256先頭6桁を大文字16進数にしたものです。machine-idを読めない場合はWi-Fi MAC addressを同じ方法でハッシュします。

PSKは初回プロファイル作成時だけ、OSのCSPRNG（`/dev/urandom`）から生成する24文字の英数字です。machine-id、MAC address、SSIDなどのGateway固有値からPSKを導出してはいけません。既存プロファイルの保存済みPSKは最優先で維持し、`OMK_AP_PSK`が指定されていても暗黙に上書きしません。既存PSKが欠落している場合だけ、`OMK_AP_PSK`または新規乱数を設定します。

PSKは`nmcli connection edit`の標準入力で設定し、argvやセットアップログへ出力しません。`--print-config`もPSK本文ではなく`[MASKED]`だけを表示します。

## setupの再実行と検証

旧AP-address Docker publishは、新socketを起動する前に解放します。移行前に稼働中production containerのimmutable image IDとpublishを保存します。移行中のbackend/socket失敗では新containerを停止し、保存したimage IDと旧publishで復旧を試みます。AP profileはdown/upしません。復旧後はimage ID一致、稼働状態、Dashboard HTTPとMQTT CONNACKを検証し、migration失敗とrollback失敗を別々に記録します。rollback失敗時は秘密情報を含まない復旧snapshotをmode 0600で残し、ログに場所を示します。新socketを確認するまで旧forwarding ruleを維持し、nft tableの置換は1 transactionで行います。

`wlan0`がfirmware crash等で一度消失すると、`BindToDevice=wlan0`のsocket unitは停止しうる。一方、`sockets.target`はすでにactiveなので、NetworkManagerの再activationだけではsocket unitは再startされない。setupは`/etc/NetworkManager/dispatcher.d/90-omk-ap-proxy-sockets`を配置する。これはNetworkManagerの`wlan0 up`イベントで、現在のconnectionが`omk-ap`であり、`192.168.50.1/24`が付与済みであることを確認してから、MQTT/Dashboard socket unitをstartする。addressやprofileが未確認なら何もbindせず終了する。常駐監視・pollingは行わない。

`./scripts/lib/validate-ap-socket-units.sh /etc/systemd/system --runtime`は、drop-inを含むunit、systemd実効property、`ss --inet-sockopt`の実listenerを検証します。IPv4/wlan0 bindingに加え実socketの`freebind`も必要です。unit変更時にはsocketとproxyを更新し、同一設定の再実行では不要なrestartを避けます。

activation workerの時間枠はpreflight 30秒、request 20秒、activation 60秒、結果確認15秒、rollback 90秒です。unitは余裕を含め260秒、停止時のcleanupは100秒を許します。rollbackは現在のwlan0接続を再確認し、別connectionへの手動復旧や既に正常なAPを維持します。非同期deactivation後は90秒のrollback共通枠内で1秒ごとにcurrent UUIDを確認し、AP UUIDが消えてから旧Wi-Fiへの復帰を要求します。第三者UUIDまたは既に復帰した旧Wi-Fiは維持します。復帰待ち中には再度connection upを発行しません。

## BLE管理APIの接続制限

`setup-ble-sensor-manager.sh`は`omk-ble-api.service`と専用nftables tableを導入します。認証を持たないTCP 8787への直接接続は、loopbackと標準Docker bridge（`docker0`、`br-*`）だけに限定します。AP、有線LAN、LTEからの直接アクセスは遮断し、利用者はDashboard経由で操作します。BLE serviceはこの制限の適用成功後に起動します。再適用はtable単位のtransactionで行い、停止時もルールを残します。独自名のDocker bridgeは正式setupの対象外です。
