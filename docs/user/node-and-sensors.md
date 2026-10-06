# OMK Nodeの役割と設置

Gatewayは計測データを集め、保存・表示する中心機器です。OMK Nodeは、市販ハードウェアのAtomS3 LiteにOMK用ファームウェアと設定を導入した端末です。センサ接続や通信の中継に使います。このページでは、計測を始めた後にNodeを追加するときの役割と設置場所を説明します。初回製作は[OMK製作ガイド](getting-started.md)、対応機種・計測項目は[対応センサ・機器と取得データ](supported-devices.md)を参照してください。

## SEN66を接続するNode

空気質センサのSEN66をOMK Nodeへ接続した構成を「SEN66 Node」と呼びます。温度・湿度・CO₂濃度などを計測し、Gatewayへデータを届けられる範囲なら、Gatewayから離れた部屋にも設置できます。

配線やケースの組み立ては[SEN66 Nodeの組み立て](sen66-node-assembly.md)を参照してください。

## BLEセンサを中継するNode

BLE中継は、Gatewayから離れたSwitchBot等のBLEセンサの電波を、近くのOMK Nodeが代わりに受信し、そのデータをGatewayへ送る機能です。BLEは、SwitchBotなどの無線センサとの通信に使う方式です。

SEN66 NodeがBLE中継を兼ねることも、SEN66を接続しない中継専用Nodeを使うこともできます。Gatewayが直接受信できるBLEセンサだけを使う場合は、Nodeは不要です。

初回登録は、対象のBLEセンサをGatewayの近くへ置いて[Dashboardで登録](dashboard.md#bleセンサを探索して登録する)します。Node経由でのみ受信している未登録センサは探索候補に表示されません。登録後に設置場所へ戻します。

## Node同士で通信を中継する

Node同士でESP-WIFI-MESHによる通信を中継し、Gatewayから離れた場所まで通信範囲を広げます。Gatewayと離れたNodeの間に、互いに通信できる範囲で別のNodeを置きます。これは、BLEセンサの電波を受けるBLE中継とは別の役割で、SEN66の計測データやNodeが受信したBLEデータをGatewayまで運ぶ経路になります。

OMKのNode間通信には、Espressifが提供する[ESP-WIFI-MESH](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/esp-wifi-mesh.html)を利用しています。ESP-WIFI-MESHはNodeが階層的につながるツリー状の構成で通信を中継する仕組みで、すべてのNode同士が常時直接通信する完全メッシュではありません。OMKでは、この仕組みを基盤として、通信が不安定になった場合の自動復旧などの機能を追加しています。

<img src="../images/esp32-node/esp32-node-network-overview.png" alt="BLEセンサをGatewayまたはOMK Nodeで受信し、Node同士はESP-WIFI-MESHでツリー状につながり、GatewayへはWi-Fiで接続する構成図" width="900">

緑の線はBLEセンサからの受信、青い線はNode間のESP-WIFI-MESHによる通信と、GatewayへのWi-Fi接続を表します。Gatewayへ向かう中継経路が構成されます。

この図はNodeとMeshの詳細図です。Bルート、表示、保存、外部通信を含む関係は[OMK製作ガイドの全体構成図](getting-started.md#omk全体のつながり)を参照してください。

## 設置場所を決める

- Nodeは、GatewayまたはGatewayへ中継できる別のNodeと通信できる位置に置きます。
- BLE中継に使うNodeは、対象センサの電波も届く位置に置きます。
- 壁や階を隔てる場所、金属で囲まれた場所では、通信が不安定になることがあります。設置後はDashboardの「機器管理」でNodeの「接続」が「オンライン」になり、中継するBLEセンサの「最終受信」が更新される位置を選びます。届かなければ、Gatewayや別のNode、センサに近づけます。
- Nodeは設置場所でUSB電源へ接続し、常時給電します。中継専用Nodeも電源を入れておきます。
- SEN66 Nodeは、SEN66の吸気・排気口を塞がないように置きます。

## Nodeを準備して使い始める

ここまででNodeの役割と設置場所を確認しました。**次へ：[AtomS3 LiteをOMK Nodeとしてセットアップする](esp32-node-setup.md)**で準備・登録・設置確認を行います。中継専用Nodeは、同ページの[中継専用Nodeを追加する](esp32-node-setup.md#中継専用nodeを追加する)へ進んでください。
