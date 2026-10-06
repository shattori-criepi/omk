# 1-1. OMK導入ガイド

おうちモニタキット（OMK）は、住宅の電力・環境・行動を計測するシステムです。データを収集・保存するOMK Gateway、計測するセンサ・機器、現在の値と機器の状態を表示するDashboardで構成します。

推奨するGateway構成は、Raspberry Pi 4、Touch Display 2（7インチ）、SORACOM Onyxです。Gatewayのセットアップ後に、空気質センサのSEN66とAtomS3 Liteを組み合わせた「SEN66 Node」を1台製作し、計測を始めます。AtomS3 Liteは市販のハードウェアで、OMK用ファームウェアと設定を書き込むと「OMK Node」になります。

## 導入の順序

| 章 | 行うこと | 章の終了時に確認すること |
| --- | --- | --- |
| 2. OMK Gatewayを製作・セットアップする | 部品を用意し、Gatewayを組み立て、OSとOMKを設定する | Dashboard、保存機能、OMK AP、Gateway側のBLE・Bルートサービスが準備でき、Gatewayのセットアップが完了している |
| 3. センサ・計測機器を追加する | SEN66 Nodeを組み立て、設定・登録し、設置する | 実際の計測値がGatewayへ届き、Dashboardの値と最終更新が更新される |
| 4. Dashboardを使う | Touch Display 2で表示設定、機器の状態確認、データ書き出しの方法を確認する | 日常の計測確認とデータの取り出しができる |

各ページの「次へ」に沿って進めてください。必要な機材は[「2-1. OMKのパーツ構成」](parts-list.md)にまとめています。SwitchBotなどのBLEセンサやBルートの計測は、SEN66の計測を確認した後、用途に合わせて追加できます。

## OMKのハードウェア構成とデータの流れ

```mermaid
flowchart LR
    sen66["SEN66"] -->|配線| node["SEN66 Node<br/>AtomS3 Lite"]
    node -->|Wi-Fi / Mesh| gw["OMK Gateway<br/>Raspberry Pi 4<br/>データ収集・保存<br/>Dashboard"]
    ble["BLEセンサ<br/>SwitchBotなど"] -->|BLE| gw
    ble -->|BLE| node
    meter["スマートメーター"] -->|Wi-SUN| adapter["RS-WSUHA-P"]
    adapter -->|USB| gw
    gw -->|DSI| display["Touch Display 2"]
    gw -->|USB| onyx["SORACOM Onyx"]
    onyx -->|LTE| external["外部通信<br/>遠隔管理・クラウド送信"]
    classDef gateway fill:#163a63,color:#fff,stroke:#071f3a,stroke-width:4px,font-size:20px;
    class gw gateway;
```

- Gatewayがセンサからデータを集めて保存し、Touch Display 2にDashboardを表示します。
- SEN66 Nodeは、配線したSEN66の計測値をWi-FiでGatewayへ送ります。図のMeshは、複数のNodeを使う場合にNode間で通信をつなぐ仕組みです。最初の1台はGatewayと通信できる位置へ設置します。
- BLEセンサはGatewayで直接受信できます。SEN66 Nodeが受信したBLEデータをGatewayへ送ることもできます。
- Bルートを使う場合は、RS-WSUHA-Pがスマートメーターと通信します。
- OnyxはGateway自身の外部通信に使います。SORACOM Harvestへの送信や遠隔管理には、利用するサービス側の設定も必要です。

対応する機器と取得データは[「1-3. 対応センサ・機器と取得データ」](supported-devices.md)で確認できます。

## OMK内部をつなぐネットワーク

図のNodeとGatewayのWi-Fi通信には、Gatewayが用意するOMK APを使います。APはAccess Point（アクセスポイント）の略です。OMK APは、Gateway・Node・管理端末を結ぶOMK専用のローカルネットワークです。

初回セットアップでは、パッケージを取得するために既存Wi-Fiでインターネットへ接続します。OMK APへ切り替えると、Raspberry Piの内蔵Wi-FiはAP専用になり、既存Wi-Fiには接続しなくなります。その後、Gateway自身の外部通信はOnyxが担います。

OMK APへ接続した端末のインターネット通信は、Gateway経由では転送しません。管理端末の通信によって意図せずSORACOMの通信量・料金が増えることを防ぎます。構築後のローカル計測・保存・表示は、住宅側ネットワークに依存せず利用できます。

次へ：[「2-1. OMKのパーツ構成」](parts-list.md)で、GatewayとSEN66 Nodeの部品・作業用品をそろえます。
