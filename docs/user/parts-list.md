# OMKの参考パーツ構成

OMKを構築するための機材一覧です。掲載している製品・購入先は参考例です。機器の対応範囲は[対応センサ・機器と取得データ](supported-devices.md)、導入の順序は[OMK製作ガイド](getting-started.md)を参照してください。

- **必須**：Gatewayを動かすために必要なものです。センサ・Nodeの表では、その用途を選んだ場合に必要なものを示します。
- **推奨**：OMKで動作確認している構成に含まれるものです。
- **任意**：設置方法や用途に合わせて追加するものです。

初回は、以下のGateway構成（ケースを含む）と初回セットアップ用品、[SEN66 Node 1台分](#sen66-nodeを使う場合)をそろえてください。Bルートや追加Node、SwitchBotの表は、後で機器を追加するときの参照用です。

## Gateway構成

OMKで動作確認している推奨構成は、Raspberry Pi 4、Raspberry Pi Touch Display 2（7インチ）、SORACOM Onyxです。

| 区分 | 部品 | 参考製品・実使用例 | 役割・選定条件 |
| --- | --- | --- | --- |
| 必須 | Gateway本体 | [Raspberry Pi 4 Model B / 2GB](https://www.switch-science.com/products/5681) | データの収集・保存とDashboardの表示 |
| 必須 | 電源 | [Raspberry Pi公式ACアダプター](https://www.switch-science.com/products/10259) | Raspberry Pi 4へ安定して給電できるもの |
| 必須 | microSDカード | SanDisk microSDXC 64GB SDSQQNR-064Gなど | OS・OMK・計測データの保存用。64GB以上を目安に、高耐久タイプを選びます。 |
| 推奨 | ディスプレイ | [Raspberry Pi Touch Display 2（7インチ）](https://www.switch-science.com/products/9940) | Gateway本体でDashboardを表示・操作 |
| 推奨 | 外部通信機器 | [SORACOM Onyx LTE USBドングル（SC-QGLC4-C1）](https://soracom.jp/store/7326/) | Gatewayのインターネット接続用 |
| Onyx利用時に必須 | SIM | [SORACOM Air SIMカード plan-D ナノSIM](https://soracom.jp/store/13380/) | 通信契約・通信料金が別途必要 |
| 推奨 | ディスプレイ用ケース | [SmartiPi Touch Pro 3 サイズS](https://smarticase.com/products/smartipi-touch-pro-3) | Raspberry Pi 4とTouch Display 2（7インチ）の両方に対応するもの |

このガイドではケースを含めて組み立てます。構成を変える際の情報は[標準構成以外・高度な構成](advanced-configuration.md)にまとめています。

### 初回セットアップに用意するもの

| 機材・環境 | 必要になる場合 |
| --- | --- |
| Windows PC | 標準手順でOSを書き込み、Gatewayを設定するとき |
| microSDカードリーダー | PCにmicroSDカードスロットがない場合 |
| インターネットに接続できる既存Wi-Fi | 初回セットアップ時 |
| USBキーボード | AP切り替え後にGateway本体でBLE・Bルートサービスを設定するとき（標準手順で使用） |
| ドライバー | ケースの付属ネジに合うもの |

具体的な操作は[Gatewayセットアップ](gateway-setup.md)を参照してください。

### 外部通信

標準構成ではOnyxと利用開始済みのSORACOM Air SIM（plan-D）を使います。事前に[SORACOMの利用開始手順](https://users.soracom.io/ja-jp/guides/getting-started/)を参照し、アカウントとSIMを用意してください。

OMK APはGateway・Node・管理端末を結ぶローカルネットワークです。AP側端末のインターネット通信はGateway経由では転送せず、外部通信はGateway自身がOnyxで行います。詳しくは[OMK APとは](getting-started.md#omk-apとは)を参照してください。

### Onyxの取り付け用部品（任意）

| 部品 | 参考製品・成果物 | 用途 |
| --- | --- | --- |
| USB回転コネクタ | [エスエスエーサービス USB A変換コネクタ（Aメス/Aオス 回転式）](https://www.amazon.co.jp/dp/B00OPYC7DK) | Onyxの向きを90度変えてGatewayの側面に沿わせる |
| Onyx固定用部品 | [onyx-usb-holder.stl](../../hardware/enclosures/omk-gateway/onyx-usb-holder.stl) | Onyxを固定する部品を3Dプリントする |

取り付け方法と注意は[OMK Gateway筐体](../../hardware/enclosures/omk-gateway/README.md)を参照してください。

## Bルートを使う場合

| 区分 | 部品・情報 | 参考製品 | 用途 |
| --- | --- | --- | --- |
| 必須 | Wi-SUN USBアダプター | [ラトックシステム RS-WSUHA-P](https://www.ratocsystems.com/products/wisun/usb-wisun/rs-wsuha/) | GatewayへUSB接続し、低圧スマートメーターの電力データを受信 |
| 必須 | Bルートの利用申し込みとID・パスワード | 電力会社から発行される情報 | スマートメーターへの接続用 |

導入・設定は[Bルートでスマートメーターを追加する](sensor-setup.md#bルートでスマートメーターを追加する)を参照してください。

## SEN66 Nodeを使う場合

温度・湿度・CO₂・PMなどを測る場合の構成です。

| 区分 | 部品 | 参考製品・実使用例 | 用途・選定条件 |
| --- | --- | --- | --- |
| 必須 | 空気質センサ | [Sensirion SEN66](https://www.digikey.jp/ja/products/detail/sensirion-ag/SEN66-SIN-T/25700945) | 空気環境の計測 |
| 必須 | 接続基板 | [Adafruit SEN6x Breakout for Sensirion SEN66 - STEMMA QT / Qwiic](https://www.digikey.jp/ja/products/detail/adafruit-industries-llc/6331/26761418) | SEN66とAtomS3 Liteの接続 |
| 必須 | 小型端末 | [M5Stack AtomS3 Lite](https://www.switch-science.com/products/8778) | センサの値をGatewayへ送信 |
| 必須 | Grove/Qwiic変換ケーブル | [Adafruit 4-PIN STEMMA/GROVE - QT/QWIIC 4"変換ケーブル](https://www.digikey.jp/ja/products/detail/adafruit-industries-llc/4528/11627737) | 接続基板とAtomS3 Liteを接続 |
| 必須 | SEN66用ケーブル | [Adafruit #5754 JST GH 1.25MM PITCH 6 PIN CABLE](https://www.digikey.jp/ja/products/detail/adafruit-industries-llc/5754/21839797) | 両端のコネクタの表裏の向きが反転したもの。代替品はAdafruit 5754と向き・配線が同じものを選びます。 |
| 必須 | USB-C to USB-Aケーブル | [Anker Zolo USB-C & USB-A ケーブル 1.0m](https://www.ankerjapan.com/products/a8052)など | 初期設定にも使うため、データ通信・給電の両方に対応したもの |
| 必須 | USB電源 | USB-A出力のACアダプター（5V、1A以上を目安） | 設置場所での常時給電 |
| 初回ガイドで使用 | ケース | [SEN66 NodeケースのSTL](../../hardware/enclosures/sen66-node/README.md) | 本体・蓋・ケーブル固定具を3Dプリントする |
| 初回ガイドで使用 | ネジと工具 | M3×8mmネジ6本、M3タップ加工用工具、ネジ頭に合うドライバー | ケースの組み立て用 |

ケースの固定具とUSBケーブルの適合条件、配線方法は[SEN66 Nodeの組み立て](sen66-node-assembly.md)を参照してください。初期設定・登録は[AtomS3 LiteをOMK Nodeとしてセットアップする](esp32-node-setup.md)を使います。

## 中継用のOMK Nodeを追加する場合（SEN66なし）

センサを置かない場所で、BLE受信やNode間の中継範囲を広げるための構成です。SEN66を接続したNodeも中継を兼ねられます。

| 区分 | 部品 | 参考製品・実使用例 | 用途・選定条件 |
| --- | --- | --- | --- |
| 必須 | 小型端末 | [M5Stack AtomS3 Lite](https://www.switch-science.com/products/8778) | BLEセンサや他のNodeの通信を中継 |
| 必須 | USB電源 | USB-A出力のACアダプター（5V、1A以上を目安） | 設置場所での常時給電 |
| どちらか必須 | 給電用の接続 | [Type-Cオス USB-Aオス変換アダプタ](https://www.amazon.co.jp/dp/B0CKVP8DZ8)、またはUSB-A → USB-Cケーブル | USB電源とAtomS3 Liteを接続 |
| 初期設定時に必須 | USBケーブル | データ通信・給電対応のUSB-A → USB-Cケーブル | Gatewayとの接続用。上の給電用ケーブルがデータ通信対応なら兼用できます。 |

設定は[AtomS3 LiteをOMK Nodeとしてセットアップする](esp32-node-setup.md)、設置場所や中継の使い方は[OMK Nodeの役割と設置](node-and-sensors.md)を参照してください。

## SwitchBot BLEセンサ（用途に応じて追加）

測定したい内容に合わせて選びます。以下は製品例で、対応機種・取得できる値・制限の一覧は[対応センサ・機器と取得データ](supported-devices.md#switchbot対応機器)を参照してください。

| 用途 | 参考製品 |
| --- | --- |
| 人感・在不在 | [SwitchBot 人感センサーPro](https://www.switchbot.jp/products/switchbot-presence-sensor) |
| ドア・窓の開閉 | [SwitchBot 開閉センサー](https://www.switchbot.jp/products/switchbot-contact-sensor) |
| 消費電力・電源状態 | [SwitchBot プラグミニ（JP）](https://www.switchbot.jp/products/switchbot-plug-mini) |
| 温度・湿度 | [SwitchBot 温湿度計](https://www.switchbot.jp/products/switchbot-meter) |
| CO₂濃度・温度・湿度 | [SwitchBot CO2センサー（Meter Pro CO2）](https://www.switchbot.jp/products/switchbot-co2-meter) |
| 温度・湿度（防水タイプ） | [SwitchBot 防水温湿度計](https://www.switchbot.jp/products/switchbot-indoor-outdoor-meter) |

登録は[Dashboardの使い方](dashboard.md#bleセンサを探索して登録する)を参照してください。

## 費用の目安

Gateway、ディスプレイ、OnyxとSIM、Bルート、SEN66 Node、追加Node、複数のBLEセンサを含む購入例では、**約10万円**です（2026年8月時点）。通信費・送料・3Dプリント費用は含みません。選ぶ構成によって総額は変わるため、購入時の価格は各販売元で確認してください。

## このページの完了と次の手順

GatewayとSEN66 Node 1台分の部品、初期設定用のPC・ネットワーク・工具を用意できたら準備完了です。

**次へ：[Gatewayの組み立て](gateway-assembly.md)で本体を組みます。**
