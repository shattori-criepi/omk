# 用語集

このページでは、OMKを使い始めるときに出てくる用語を短く説明します。

| 用語 | OMKでの意味 |
| --- | --- |
| Gateway | 計測データを集め、保存・表示するOMKの中心機器です。Raspberry Piを使います。 |
| Dashboard | ブラウザやTouch Display 2で、計測値の表示、センサ登録、設定を行う画面です。 |
| OMK AP（Wi-Fiアクセスポイント） | Gatewayが提供するOMK用Wi-Fiです。OMK NodeやPCなどをGatewayへ接続するために使い、インターネット接続用ではありません。 |
| SSID | Wi-Fiの接続先を区別する名前です。PCなどのWi-Fi一覧に表示されます。 |
| SSH | PCからGatewayへ接続し、コマンドを実行するための方法です。 |
| OMK Node | AtomS3 Liteを使い、Gatewayから離れた場所のセンサ接続やBLE中継を行う小型機器です。 |
| AtomS3 Lite | OMK Nodeとして使用する小型の機器です。 |
| センサ | 温度・湿度・電力などを測ったり、人の動きやドアの開閉などを検知したりする機器です。 |
| SEN66 Node | 空気質センサのSEN66を接続したOMK Nodeです。温度・湿度・CO₂濃度などを計測します。 |
| BLE | Bluetooth Low Energyの略で、近距離の無線通信方式です。OMKでは対応するSwitchBotなどのセンサから情報を受信するために使います。 |
| BLE中継 | OMK NodeがBLEセンサの情報を受信し、Gatewayへ届ける機能です。 |
| ESP-WIFI-MESH / Mesh | OMK Node同士が階層的に通信を中継し、Gatewayから離れた場所まで通信範囲を広げる仕組みです。 |
| Logical ID | Nodeに接続したSEN66を識別する名前です。Dashboardや計測データで使います。 |
| ファームウェア | 機器に書き込んで使うソフトウェアです。OMK Nodeでは、センサの計測や通信を動かします。 |
| Bルート | 低圧スマートメーターから電力データを取得するための通信方式です。利用には電力会社から発行されるIDとパスワードが必要です。 |
| SORACOM Onyx | GatewayへUSB接続し、携帯電話回線でインターネットへ接続する機器です。 |
| SORACOM Air SIM | Onyxへ挿入して携帯電話回線を使うためのSIMカードです。通信契約と利用開始が必要です。 |
| APN | 携帯電話回線の接続先を指定する設定です。使用するSIMの種類に合わせます。 |
| SORACOM Napter | 離れた場所のPCからGatewayへ接続し、操作するために使うSORACOMのサービスです。 |
| SORACOM Harvest | 計測データをインターネット経由で送信し、クラウドに保存できるSORACOMのサービスです。 |

機器の組み合わせは[OMK Nodeの役割と設置](node-and-sensors.md)、対応機種と計測項目は[対応センサ・機器と取得データ](supported-devices.md)、画面操作は[Dashboardの使い方](dashboard.md)を参照してください。
