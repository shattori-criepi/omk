# 用語集

このページでは、OMKを使い始めるときに出てくる用語を短く説明します。

| 用語 | OMKでの意味 |
| --- | --- |
| Gateway | 計測データを集め、保存・表示するOMKの中心機器です。Raspberry Piを使います。 |
| Dashboard | ブラウザやTouch Display 2で、計測値の表示、センサ登録、設定を行う画面です。 |
| OMK AP（Wi-Fiアクセスポイント） | Gateway・OMK Node・管理用PCなどをつなぐ、OMK専用のローカルネットワークです。接続したPCやNodeの通信をGateway経由でインターネットへ転送しません。外部通信が必要な場合はGateway自身がOnyx等を使うため、AP側端末の通信でSORACOM回線を意図せず消費しない構成です。 |
| SSID | Wi-Fiの接続先を区別する名前です。PCなどのWi-Fi一覧に表示されます。 |
| SSH | PCからGatewayへ接続し、コマンドを実行するための方法です。 |
| AtomS3 Lite | M5Stackが販売する小型のハードウェア製品です。OMK用のファームウェアと設定を導入して使います。 |
| OMK Node | AtomS3 LiteをOMK用のファームウェアと設定でセットアップした端末です。OMK内でセンサ接続、BLE中継、Node間のMesh中継を行います。 |
| センサ | 温度・湿度・電力などを測ったり、人の動きやドアの開閉などを検知したりする機器です。 |
| SEN66 Node | 空気質センサのSEN66を接続したOMK Nodeです。温度・湿度・CO₂濃度などを計測します。 |
| BLE | Bluetooth Low Energyの略で、近距離の無線通信方式です。OMKでは対応するSwitchBotなどのセンサから情報を受信するために使います。 |
| BLE中継 | Gatewayから離れたSwitchBot等のBLEセンサの電波を、近くのOMK Nodeが代わりに受信し、そのデータをGatewayへ送る機能です。 |
| ESP-WIFI-MESH / Mesh | OMK Node同士が階層的に通信を中継し、Gatewayから離れた場所まで通信範囲を広げる仕組みです。BLEセンサの電波を受けるBLE中継とは別で、Nodeが計測・受信したデータをGatewayへ運びます。 |
| Logical ID | Nodeに接続したSEN66を識別する名前です。Dashboardや計測データで使います。 |
| ファームウェア | 機器に書き込んで使うソフトウェアです。OMK Nodeでは、センサの計測や通信を動かします。 |
| Bルート | 低圧スマートメーターから電力データを取得するための通信方式です。利用には電力会社から発行されるIDとパスワードが必要です。 |
| SORACOM Onyx | GatewayへUSB接続し、携帯電話回線でインターネットへ接続する機器です。 |
| SORACOM Air SIM | Onyxへ挿入して携帯電話回線を使うためのSIMカードです。通信契約と利用開始が必要です。 |
| APN | 携帯電話回線の接続先を指定する設定です。使用するSIMの種類に合わせます。 |
| SORACOM Napter | 離れた場所のPCからGatewayへ接続し、操作するために使うSORACOMのサービスです。 |
| SORACOM Harvest | 計測データをインターネット経由で送信し、クラウドに保存できるSORACOMのサービスです。 |

用語を確認できたら、初回製作は[OMK製作ガイド](getting-started.md)、本体完成後の機器追加は[センサ・計測機器を追加する](sensor-setup.md)へ戻って続きを進めます。Nodeの配置は[OMK Nodeの役割と設置](node-and-sensors.md)、対応機種と計測項目は[対応センサ・機器と取得データ](supported-devices.md)、画面操作は[Dashboardの使い方](dashboard.md)を参照してください。
